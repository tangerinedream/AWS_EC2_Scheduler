#!/usr/bin/python
import boto3
import logging
import json
import time
import string
from distutils.util import strtobool

__author__ = "Gary Silverman"

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
	DECISION_RETRIES_EXCEEDED='RetriesExceeded'
	DECISION_S3_RESULTFILE_NOT_LOCATED='s3 file not located'
	DECISION_NO_ACTION_UNEXPECTED_RESULT='unexpectedResult'

	SCRIPT_STOP_INSTANCE='Stop'
	SCRIPT_NO_ACTION='Bypass'  # Bypass means skip stopping this instance

	S3_BUCKET_LOCATION_NOT_YET_DETERMINED = 'unset'
	S3_BUCKET_IN_WRONG_REGION='BadS3BucketRegion'
	S3_BUCKET_IN_CORRECT_REGION='GoodS3BucketRegion'

	OS_TYPE_LINUX = 'Linux'
	OS_TYPE_WINDOWS = 'Windows'

	SSM_COMMAND_ID = 'CommandId'


	def __init__(self, instanceId, bucketName, keyPrefixName, fileURI, osType, ddbRegion, logger, workloadRegion='us-west-2'):

		self.instanceId=instanceId

		self.ddbRegion=ddbRegion

		self.workloadRegion=workloadRegion

		self.logger = logger

		try:
			self.ssm = boto3.client('ssm', region_name=self.workloadRegion)
		except Exception as e:
			msg = 'SSMDelegate::__init__() Exception obtaining botot3 ssm resource in region %s -->' % workloadRegion
			self.logger.error(msg + str(e))

		self.ssmDocumentName=''

		self.connectionTimeout=180

		# The duration to sleep while awaiting results from the SSM Command executing
		self.retrieveSSMResultSleepDuration=10

		# Max wait is 5 minutes (10 seconds * 18 = 180 seconds or 3 minutes)
		self.getResultRetryCount = 18

		self.S3BucketName=bucketName

		self.S3KeyPrefixName=keyPrefixName

		# The unique identifier of the ssm command
		self.commandId = ''

		# The name of the Override File to test for existence
		self.fileURI = fileURI

		# Copy of which OS type is being used
		self.osType = osType

		# At the time of writing, SSM only outputs to an S3 bucket in the same region as the target instance.
		self.S3BucketInWorkloadRegion = SSMDelegate.S3_BUCKET_LOCATION_NOT_YET_DETERMINED

		try:
			self.s3 = boto3.client('s3', region_name=self.workloadRegion)
		except Exception as e:
			msg = 'SSMDelegate::__init__() Exception obtaining botot3 s3 resource in region %s -->' % workloadRegion
			self.logger.error(msg + str(e))

	def sendSSMCommand(self):

		#Capture osType so we can lookup SSM results in S3 correctly
		if( self.osType == SSMDelegate.OS_TYPE_WINDOWS ):
			self.ssmDocumentName='AWS-RunPowerShellScript'
			testOverrideFileCommand='IF ((test-path ' + self.fileURI + ') -eq $True) { echo '+ self.SCRIPT_NO_ACTION +' } ELSE { echo '+ self.SCRIPT_STOP_INSTANCE +' }'
			defaultDir='C:'
		else: #default to OS_TYPE_LINUX
			self.ssmDocumentName='AWS-RunShellScript'
			testOverrideFileCommand='if [ -e ' + self.fileURI + ' ]; then echo \"'+ self.SCRIPT_NO_ACTION +'\"; else echo \"'+ self.SCRIPT_STOP_INSTANCE +'\"; fi'
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
				self.logger.debug('ssm send_command response (key==%s, value==%s)' % (key, value))

			self.logger.debug('SSMDelegate send_command() results :')
			self.logger.debug('Operating System: ' + self.osType)
			self.logger.debug('Command attempted: ' + testOverrideFileCommand)
			self.logger.debug('CommandId:  ' + self.getAttributeFromSSMSendCommand(response, 'CommandId'))
			self.logger.debug('Instance List:  ' + str(self.getAttributeFromSSMSendCommand(response, 'InstanceIds')))

		except Exception as e:
			self.logger.warning('sendSSMCommand() Exception occurred. Please ensure DynamoDB table has correct Operating System for  TierStopOverrideOperatingSystemSSM attribute and SSM agent is installed on instance, \
and instance is running with Instance Profile (see documentation).  Exception was: ' + str(e) )
			response=''

		return( response )




	###
	# Retrieve the result by looking within the S3 bucket
	###
	def retrieveSSMResults(self, ssmResponse):

		result = SSMDelegate.DECISION_NO_ACTION  # By default, we will not shut down the instance

		self.commandId = self.getAttributeFromSSMSendCommand(ssmResponse, SSMDelegate.SSM_COMMAND_ID)

		# Do we have a commandId from the sendCommand response?
		if self.commandId:

			try:

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
							# Due to cross platform treatment of text files, chomp everything that isn't alphanumeric
							scriptRes = filter(str.isalnum, self.lookupS3Result())

							# If the string says Continue, then it's a go.  Otherwise, we won't stop it.
							if( scriptRes == SSMDelegate.SCRIPT_STOP_INSTANCE ):
								result = SSMDelegate.DECISION_STOP_INSTANCE
                                                        elif( scriptRes == SSMDelegate.DECISION_S3_RESULTFILE_NOT_LOCATED ):
								result = SSMDelegate.DECISION_S3_RESULTFILE_NOT_LOCATED 
						else:
							# Wasn't Success, so let's output what it was
							self.logger.warning('SSM response completed but not as "Success".  SSM result was ' + str(res) )

					else:
						# So we aren't doing this forever
						counter += 1
						self.logger.info('SSMDelegate::retrieveSSMResults() Awaiting Completed Status. Sleep and retry #' + str(counter))
						time.sleep(self.retrieveSSMResultSleepDuration)

			except Exception as e:
				self.logger.warning('Encountered an exception retrieving SSM Results -->' + str(e))

		else:
			self.logger.warning('Could not find CommandId in response for InstanceId: ' + self.instanceId)
			self.logger.warning('SSMResponse was: %s' % str(ssmResponse))


		# Default is DECISION_NO_ACTION
		if( counter >= self.getResultRetryCount):
			self.logger.warning('Max retries exceeded for collecting results from S3, so InstanceId: ' + self.instanceId +' will not be stopped')
			result=SSMDelegate.DECISION_RETRIES_EXCEEDED
		elif( result == SSMDelegate.DECISION_STOP_INSTANCE ):
			self.logger.info('InstanceId: ' + self.instanceId + ' will be stopped')
		elif( result == SSMDelegate.DECISION_S3_RESULTFILE_NOT_LOCATED ):
			self.logger.info('SSM Command for InstanceId: ' + self.instanceId + ' completed, however, the results file is not locatable in s3.  As a precaution, the instance will NOT be stopped')
		elif( result == SSMDelegate.DECISION_NO_ACTION ):
			self.logger.info('InstanceId: ' + self.instanceId + ' has override file and will NOT be stopped')
		else:
			result == SSMDelegate.DECISION_NO_ACTION_UNEXPECTED_RESULT
			self.logger.warning('InstanceId: ' + self.instanceId + ' unexpected SSM result ==>'+ result +'<==, or inaccessible instance.  Instance will NOT be stopped')

		return( result )

	def makeS3Key(self, version=1):
		# bucketName/keyPrefixName+region/commandId/instance-id/awsrunShellScript/0.aws:runShellScript
		delimiter='/'

		if( self.osType == SSMDelegate.OS_TYPE_WINDOWS ):
			# Note: Do not prefix 'key' with leading slash
			if(version==2):
				key= \
					self.S3KeyPrefixName \
					+delimiter+self.commandId \
					+delimiter+self.instanceId \
					+delimiter+'awsrunPowerShellScript' \
					+delimiter +'0.awsrunPowerShellScript' \
					+delimiter+'stdout'  # Note: for some reason the .txt is dropped in this version as well
			else:  # includes version == 1
				key = \
					self.S3KeyPrefixName \
					+ delimiter + self.commandId \
					+ delimiter + self.instanceId \
					+ delimiter + 'awsrunPowerShellScript' \
					+ delimiter + 'stdout.txt'

		else:
			# Note: Do not prefix 'key' with leading slash
			key= \
				self.S3KeyPrefixName \
				+delimiter+self.commandId \
				+delimiter+self.instanceId \
				+delimiter+'awsrunShellScript' \
				+delimiter+'0.aws:runShellScript' \
				+delimiter+'stdout'

		return(key)

	def isS3BucketInWorkloadRegion(self):

		result = SSMDelegate.S3_BUCKET_LOCATION_NOT_YET_DETERMINED
		if( self.S3BucketInWorkloadRegion == SSMDelegate.S3_BUCKET_LOCATION_NOT_YET_DETERMINED ):
			# Per SSM, the S3 output bucket must be in the same region as the target instance

			# Determine region of the target bucket
			try:
				S3BucketLocRes = self.s3.get_bucket_location(Bucket=self.S3BucketName)
				if( 'LocationConstraint' in S3BucketLocRes ):
					S3BucketLoc = S3BucketLocRes['LocationConstraint']

					# Determine if the same as the workload region.
					# If it is not, then SSM will not log the result of the command execution
					# and we won't be able to ascertain if the command ran successfully
					if( S3BucketLoc == self.workloadRegion ):
						result = SSMDelegate.S3_BUCKET_IN_CORRECT_REGION
						self.logger.debug('Bucket Region is %s Workload Region is %s ' % (S3BucketLoc, self.workloadRegion))

					elif( (S3BucketLoc == None) and ( self.workloadRegion == 'us-east-1' ) ):
						result = SSMDelegate.S3_BUCKET_IN_CORRECT_REGION
						self.logger.debug('Bucket Region is %s Workload Region is %s ' % (S3BucketLoc, self.workloadRegion))

					else:
						result = SSMDelegate.S3_BUCKET_IN_WRONG_REGION
						self.logger.warning('The S3 bucket cannot be confirmed to be in the Workload Region. SSM will not log the results of commands to buckets outside of the instances region. As such, no instances will be stopped, since the SSM result checking for the override file cannot be determined.')
						self.logger.warning('Bucket Region is %s Workload Region is %s ' % (S3BucketLoc, self.workloadRegion))

				else:
					result = SSMDelegate.S3_BUCKET_IN_WRONG_REGION
					self.logger.warning('The S3 bucket cannot be confirmed to be in the Workload Region. SSM will not log the results of commands to buckets outside of the instances region. As such, no instances will be stopped, since the SSM result checking for the override file cannot be determined.')
					self.logger.warning('Bucket Region is %s Workload Region is %s ' % (S3BucketLoc, self.workloadRegion))

			except Exception as e:
				self.logger.error('isS3BucketInWorkloadRegion() '+ str(e) )
				response = SSMDelegate.S3_BUCKET_IN_WRONG_REGION

		self.S3BucketInWorkloadRegion=result
		return(result)



	def lookupS3Result(self):
		content=''
		# Return the output result from the script execution


		try:

			key = self.makeS3Key()
			found = False

			if (self.osType == SSMDelegate.OS_TYPE_WINDOWS):  # only need to do this for windows

				# First, check for existence in default (e.g. SSM version == 1 ) location
				result = self.s3.list_objects_v2(
					Bucket=self.S3BucketName,
					Prefix=key
				)
				# If not there and if Windows, check in the second location (e.g. SSM version == 2) location
				if( result['KeyCount'] == 0 ):
					self.logger.info("Could not locate SSM result file in S3, for key :" + key + ": Will now try new loacation...")
					version = 2
					key = self.makeS3Key(version)

					result = self.s3.list_objects_v2(
						Bucket=self.S3BucketName,
						Prefix=key
					)
					if( result['KeyCount'] == 0 ):
						self.logger.warning("Could not locate SSM result file in S3 using new keyname structure, for key :" + key)
					else:
						found = True
				else:
					found = True

			else:
				result = self.s3.list_objects_v2(
					Bucket=self.S3BucketName,
					Prefix=key
				)
				if (result['KeyCount'] > 0):
					found = True
				else:
					self.logger.warning("Could not locate SSM result file in S3 :" + key)

			if(found):
				# Get the S3 object now
				result = self.s3.get_object(
					Bucket=self.S3BucketName,
					Key=key
				)
                        else:
                                result = ""
                                content = SSMDelegate.DECISION_S3_RESULTFILE_NOT_LOCATED


		except Exception as e:
			self.logger.warning('Could not lookup SSM results in S3.  Exception was -->' + str(e))
			return (content)


		try:
			# Locate the Content in the response
			if 'Body' in result:
				stream = result['Body']

				# Read the content
				content = stream.read()

		except Exception as e:
			self.logger.warning('Found but could not read SSM results from S3.  Exception was -->' + str(e))

		return(content)

	def getAttributeFromSSMSendCommand(self, ssmResponse, attributeName):
		result=''

		if ssmResponse:
			if 'Command' in ssmResponse:
				commandDict = ssmResponse['Command']
				if attributeName in commandDict:
					result = commandDict[attributeName]
		else:
			self.logger.warning('No ssmResponse retrieved.')

		return(result)


	def getStatusFromSSMListCommands(self, ssmResponse, commandId):
		status=''

		if ssmResponse:
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
		else:
			self.logger.warning('No ssmReponse provided to check')

		return( status )

	def runTestCases(self):
		#doc = self.makeSSMRunDocument("/tmp/override")
		docResult=self.sendSSMCommand('/tmp/override')
		if docResult:
			overrideRes=self.retrieveSSMResults(docResult)
			self.logger.info('SSMDelegate runTestCases() results :' + overrideRes)
		else:
			self.logger.info('SSMDelegate runTestCases() instance inaccessible, bypassing')

if __name__ == "__main__":

	loggerNameStr='SSMDelegate'
	logger = logging.getLogger(loggerNameStr)  #The Module Name
	logger.setLevel(logging.INFO)
	logging.basicConfig(format='%(asctime)s:%(levelname)s:%(name)s==>%(message)s', filename=loggerNameStr + '.log', filemode='w', level=logging.INFO)


	ssm = SSMDelegate('i-074e5f7e93fdaaa50', 'com.gman.ssm', 'ssmRemoteComandResults', logger, 'us-west-2')
	ssm.runTestCases()

