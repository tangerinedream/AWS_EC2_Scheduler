#!/usr/bin/python
import boto3
import logging
import json
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
	

	def __init__(self, instanceId, bucketName, keyPrefixName, region_name='us-west-2'):

		self.instanceId=instanceId

		self.region_name=region_name
		
		self.ssm = boto3.client('ssm', self.region_name)

		self.ssmDocumentName=""

		self.connectionTimeout=180

		self.S3BucketName=bucketName
		    
		self.S3KeyPrefixName=keyPrefixName

		self.initLogging()
		


	def sendSSMCommand(self, fileURI):

		self.ssmDocumentName='AWS-RunShellScript'
		testOverrideFileExistenceLinuxCmd='if [ -e ' + fileURI + ' ]; then echo \"Override\"; else echo \"Continue\"; fi'		
		defaultDir='/tmp'

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
		            testOverrideFileExistenceLinuxCmd,
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
		self.logger.info('SSMDelegate send_command() results :')
		for key, value in response.iteritems():
			self.logger.info('(key==%s, value==%s)' % (key, value))

		return( response )

	###
	# Retrieve the result by looking within the S3 bucket
	###
	def retrieveSSMResults(self):
		pass

	def initLogging(self):
		# Setup the Logger
		loggerNameStr='SSMDelegate'
		self.logger = logging.getLogger(loggerNameStr)  #The Module Name
		self.logger.setLevel(logging.INFO)
		logging.basicConfig(format='%(asctime)s:%(levelname)s:%(name)s==>%(message)s', filename=loggerNameStr + '.log', filemode='w', level=logging.INFO)

	def runTestCases(self):
		#doc = self.makeSSMRunDocument("/etc/override")
		docResult=self.sendSSMCommand('/etc/override')
		print docResult

if __name__ == "__main__":
	# Issue could be the region
	ssm = SSMDelegate('i-074e5f7e93fdaaa50', 'com.gman.ssm', 'ssmRemoteComandResults_' 'us-west-2')
	ssm.runTestCases()

