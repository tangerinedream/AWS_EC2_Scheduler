#!/usr/bin/python
import boto3
import logging
import json
import time
from distutils.util import strtobool

class SSMDelegate(object):
	### 
	# 
	# Class Variables
	#
	###
	
	# Use the following command to view a description of the SSM JSON document.
	# aws ssm describe-document --name "AWS-RunShellScript" --query "[Document.Name,Document.Description]"

	# Use the following command to view the available parameters and details about those parameters.
	# aws ssm describe-document --name "AWS-RunShellScript" --query "Document.Parameters[*]"

	# Use the following command to get IP information for an instance.
	# aws ssm send-command --instance-ids "instance ID" --document-name "AWS-RunShellScript" --comment "IP config" --parameters commands=ifconfig --output text


	# aws ssm send-command 
	# 	--instance-ids "i-06bae75b40e7539e3" 
	# 	--document-name "AWS-RunShellScript" 
	# 	--comment "Test stopping 'Tomcat' service" 
	# 	--parameters '{"commands":["service tomcat7 stop"],"executionTimeout":["3600"]}' 
	# 	--timeout-seconds 600 
	# 	--region us-east-1 
	# 	--query "Command.CommandId"

	DECISION_STOP_INSTANCE='Stop'
	DECISION_NO_ACTION='Bypass'

	SCRIPT_STOP_INSTANCE='Stop'
	SCRIPT_NO_ACTION='Bypass'  # Bypass means skip stopping this instance

	OS_TYPE_LINUX = 'Linux'
	OS_TYPE_WINDOWS = 'Windows'

	SSM_COMMAND_ID = 'CommandId'
	

	def __init__(self, instanceId, bucketName, keyPrefixName, region_name='us-west-2'):

		self.instanceId=instanceId

		self.region_name=region_name
		
		self.ssm = boto3.client('ssm', self.region_name)

		self.ssmDocumentName=''

		self.connectionTimeout=60

		# The duration to sleep while awaiting results from the SSM Command executing
		self.retrieveSSMResultSleepDuration=10

		# Max wait is 5 minutes (10 seconds * 30 = 300 seconds or 5 minutes)
		self.getResultRetryCount = 30

		self.S3BucketName=bucketName
		    
		self.S3KeyPrefixName=keyPrefixName

		# The unique identifier of the ssm command
		self.commandId = ''

		self.s3 = boto3.client('s3', self.region_name)

		self.initLogging()
		


	def sendSSMCommand(self, fileURI, osType):

		# defaults to Linux
		if( osType == SSMDelegate.OS_TYPE_WINDOWS ):
			self.ssmDocumentName='AWS-RunPowerShellScript'
			testOverrideFileCommand='@echo off & IF exist ' + fileURI + ' ( echo '+ self.SCRIPT_NO_ACTION +' ) ELSE ( echo '+ self.SCRIPT_STOP_INSTANCE +' )'
			defaultDir='C:'
		else: #default to OS_TYPE_LINUX
			self.ssmDocumentName='AWS-RunShellScript'
			testOverrideFileCommand='if [ -e ' + fileURI + ' ]; then echo \"'+ self.SCRIPT_NO_ACTION +'\"; else echo \"'+ self.SCRIPT_STOP_INSTANCE +'\"; fi'		
			defaultDir='/tmp'

		try:
			# the 'commands' is the name of the property within the AWS-RunShellScript
			# the TimeoutSeconds is the time to reach the instance, not execution time to run
			#   the commands within the instance once reached.
			# For more details: Open the AWS-RunShellScript document within the management
			#   console, where you can inspect the actual document under the 'Content' tab. 
			response = self.ssm.send_command(
			    InstanceIds=[
			        self.instanceId,
			    ],
			    Parameters={
			        'commands': [
			            testOverrideFileCommand,
			        ],
			        'workingDirectory' : [
			        	defaultDir,
			        ],
			    },
			    DocumentName=self.ssmDocumentName,
			    TimeoutSeconds=self.connectionTimeout,
			    OutputS3BucketName=self.S3BucketName,
			    OutputS3KeyPrefix=self.S3KeyPrefixName,
			    Comment='Send command to test if override file exists on instance'
			)
			# Results are in:
			#   bucket->keyPrefix+region->commandId-->instanceId-->awsrunshellscript-->0.aws.runshellscript-->stdout

			# Output
			for key, value in response.iteritems():
				self.logger.debug('(key==%s, value==%s)' % (key, value))

			self.logger.info('SSMDelegate send_command() results :')	
			self.logger.info('Operating System: ' + osType)
			self.logger.info('Command attempted: ' + testOverrideFileCommand)	
			self.logger.info('CommandId:  ' + self.getAttributeFromSSMSendCommand(response, 'CommandId'))
			self.logger.info('Instance List:  ' + str(self.getAttributeFromSSMSendCommand(response, 'InstanceIds')))

		except Exception as e:
			self.logger.error('ssm send exception: ')
			self.logger.error(e)
			response=''

		return( response )




	###
	# Retrieve the result by looking within the S3 bucket
	###
	def retrieveSSMResults(self, ssmResponse):
		#
		# Result will look like this
		#
		# SSMDelegate==>(key==Command, value=={
		# 	u'Comment': u'Send command to test if override file exists on instance', 
		# 	u'Status': u'Pending', 
		# 	u'Parameters': {
		# 		u'commands': [u'if [ -e /etc/override ]; then echo "Bypass"; else echo "Stop"; fi'], u'workingDirectory': [u'/tmp']
		# 	},
		# 	u'ExpiresAfter': datetime.datetime(2016, 5, 18, 23, 9, 32, 227000, tzinfo=tzlocal()), 
		# 	u'DocumentName': u'AWS-RunShellScript', 
		# 	u'OutputS3BucketName': u'com.gman.ssm', 
		# 	u'OutputS3KeyPrefix': u'ssmRemoteComandResultsus-west-2', 
		# 	u'RequestedDateTime': datetime.datetime(2016, 5, 18, 23, 6, 32, 227000, tzinfo=tzlocal()), 
		# 	u'CommandId': u'5a7ab448-a296-44f6-b24a-379f9b669cd9', 
		# 	u'InstanceIds': [u'i-074e5f7e93fdaaa50']
		# 	}
		# )
		# SSMDelegate==>(key==ResponseMetadata, value=={
		# 	'HTTPStatusCode': 200, 
		# 	'RequestId': '11fd85e4-1d77-11e6-b1fd-69d231a5ab1b'
		# 	}
		# )
		#
		result = self.DECISION_NO_ACTION  # By default, we will not shut down the instance
		
		self.commandId = self.getAttributeFromSSMSendCommand(ssmResponse, SSMDelegate.SSM_COMMAND_ID)

		# Do we have a commandId from the sendCommand response?
		if self.commandId:		

			# Let's try to get the response, and wait if it isn't ready
			done=False
			counter=0
			while( (not done) and (counter < self.getResultRetryCount ) ):
				response = self.ssm.list_commands(
					CommandId=self.commandId,
					InstanceId=self.instanceId
				)

				#
				self.logger.debug('SSMDelegate list_commands() results :')
				for key, value in response.iteritems():
					self.logger.debug('(key==%s, value==%s)' % (key, value))
				
				# pull out the status
				res = self.getStatusFromSSMListCommands(response, self.commandId)

				# check to see if status is done
				#    done mean status in 'Success'|'TimedOut'|'Cancelled'|'Failed'
				if( res in ['Success', 'TimedOut', 'Cancelled', 'Failed'] ):
					done=True

					# Great, but is there a result to retrieve?
					if( res == 'Success' ):

						# Ok, let's go look it up in S3
						# Since the echo in the script adds a newline, chomp it off
						scriptRes=self.lookupS3Result().rstrip('\n')

						# If the string says Continue, then it's a go.  Otherwise, we won't stop it. 
						if( scriptRes == self.SCRIPT_STOP_INSTANCE ):
							result = self.DECISION_STOP_INSTANCE

				# So we aren't doing this forever
				counter += 1
				self.logger.info('SSMDelegate::retrieveSSMResults() Awaiting Completed Status. Sleep and retry #' + str(counter))
				time.sleep(self.retrieveSSMResultSleepDuration)


		else:
			self.logger.warning('Could not find CommandId in response for InstanceId: ' + self.instanceId)
			self.logger.warning('SSMResponse was: %s' % str(ssmResponse))

		# Default is DECISION_NO_ACTION
		if( result == self.DECISION_STOP_INSTANCE ):
			self.logger.info('InstanceId: ' + self.instanceId + ' will be stopped')
		elif( result == self.DECISION_NO_ACTION ):
			self.logger.info('InstanceId: ' + self.instanceId + ' has override file and will NOT be stopped')
		else:
			self.logger.info('InstanceId: ' + self.instanceId + ' unexpected SSM result ==>'+ result +'<==, or inaccessible instance.  Instance will NOT be stopped')

		return( result )

	def makeS3Key(self):
		# bucketName/keyPrefixName+region/commandId/instance-id/awsrunShellScript/0.aws:runShellScript
		delimiter='/'
		
		# Note: Do not prefix 'key' with leading slash
		key= \
			self.S3KeyPrefixName \
			+delimiter+self.commandId \
			+delimiter+self.instanceId \
			+delimiter+'awsrunShellScript' \
			+delimiter+'0.aws:runShellScript' \
			+delimiter+'stdout'

		return(key)

	def lookupS3Result(self):
		content=''
		# Return the output result from the script execution

		# First, construct the location
		key = self.makeS3Key()

		# 
		result = self.s3.get_object(
			Bucket=self.S3BucketName,
		    Key=key
		)

		# Locate the Content in the response
		if 'Body' in result:
			stream = result['Body']

			# Read the content
			content = stream.read()

		return(content)

	def getAttributeFromSSMSendCommand(self, ssmResponse, attributeName):
		result=''

		if 'Command' in ssmResponse:			
			commandDict = ssmResponse['Command']
			if attributeName in commandDict:
				result = commandDict[attributeName]			
		
		return(result)


	def getStatusFromSSMListCommands(self, ssmResponse, commandId):
		status=''
		
		if 'Commands' in ssmResponse:			
			commandsList = ssmResponse['Commands']

			# Find the correct result
			for curr in commandsList:

				# Is this a valid CommandList item?
				if( 'CommandId' in curr and 'Status' in curr ):
					currCommandId = curr['CommandId']
					
					# Did we get the right one?
					if( currCommandId == commandId ):
						status = curr['Status']

						# We are done here
						break
		
		return( status )

	def initLogging(self):
		# Setup the Logger
		loggerNameStr='SSMDelegate'
		self.logger = logging.getLogger(loggerNameStr)  #The Module Name
		self.logger.setLevel(logging.INFO)
		logging.basicConfig(format='%(asctime)s:%(levelname)s:%(name)s==>%(message)s', filename=loggerNameStr + '.log', filemode='w', level=logging.INFO)

	def runTestCases(self):
		#doc = self.makeSSMRunDocument("/tmp/override")
		docResult=self.sendSSMCommand('/tmp/override')
		if docResult:
			overrideRes=self.retrieveSSMResults(docResult)
			self.logger.info('SSMDelegate runTestCases() results :' + overrideRes)
		else:
			self.logger.info('SSMDelegate runTestCases() instance inaccessible, bypassing')

if __name__ == "__main__":
	# Issue could be the region
	ssm = SSMDelegate('i-074e5f7e93fdaaa50', 'com.gman.ssm', 'ssmRemoteComandResults', 'us-west-2')
	ssm.runTestCases()

