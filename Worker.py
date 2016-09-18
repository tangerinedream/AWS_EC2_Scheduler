#!/usr/bin/python
import boto3
import logging
from distutils.util import strtobool
from SSMDelegate import SSMDelegate

__author__ = "Gary Silverman"

class Worker(object):
	SNS_SUBJECT_PREFIX_WARNING="Warning:"
	SNS_SUBJECT_PREFIX_INFORMATIONAL="Info:"

	def __init__(self, workloadRegion, instance, logger, dryRunFlag):

		self.workloadRegion=workloadRegion
		self.ec2Resource = boto3.resource('ec2', region_name=self.workloadRegion)
		self.instance=instance
		self.dryRunFlag=dryRunFlag
		self.logger = logger

		self.instanceStateMap = {
			"pending" : 0, 
			"running" : 16,
			"shutting-down" : 32,
			"terminated" : 48,
			"stopping" : 64,
			"stopped" : 80
		}
		# self.initLogging()

	def initLogging(self):
		pass
		# Setup the Logger
		# self.logger = logging.getLogger('Worker')  #The Module Name
		# self.logger.setLevel(logging.INFO)
		# logging.basicConfig(format='%(asctime)s:%(levelname)s:%(name)s==>%(message)s', filename='Worker' + '.log', filemode='w', level=logging.INFO)
		
		###
		#Currently, this adds another logger everytime a subclass instantiated.

		# Setup the Handlers
		# create console handler and set level to debug
		# consoleHandler = logging.StreamHandler()
		# consoleHandler.setLevel(logging.INFO)
		# self.logger.addHandler(consoleHandler)



	''' probably add some convenience methods to update DynamoDB or Log Files with progress/status '''


class StartWorker(Worker):
	def __init__(self, ddbRegion, workloadRegion, instance, logger, dryRunFlag):
		super(StartWorker, self).__init__(workloadRegion, instance, logger, dryRunFlag)

		self.ddbRegion=ddbRegion

	def startInstance(self):

		result=''
		if( self.dryRunFlag ):
			self.logger.warning('DryRun Flag is set - instance will not be started')
		else:
			#EC2.Instance.start()
			result=self.instance.start()
		
		self.logger.info('startInstance() for ' + self.instance.id + ' result is %s' % result)

	def execute(self):
		self.startInstance()





class StopWorker(Worker):
	def __init__(self, ddbRegion, workloadRegion, snsNotConfigured, snsTopic, snsTopicSubject, instance, logger, dryRunFlag):
		super(StopWorker, self).__init__(workloadRegion, instance, logger, dryRunFlag)
		
		self.ddbRegion=ddbRegion

		self.snsTopic = snsTopic
		self.snsTopicSubject = snsTopicSubject

		# MUST convert string False to boolean False
		self.waitFlag=strtobool('False')
		self.overrideFlag=strtobool('False')
		self.snsNotConfigured=snsNotConfigured

		
	def stopInstance(self):

		self.logger.debug('Worker::stopInstance() called')
		result=''
		
		if( self.dryRunFlag ):
			self.logger.warning('DryRun Flag is set - instance will not be stopped')
		else:
			#EC2.Instance.stop()
			result=self.instance.stop()

		# If configured, wait for the stop to complete prior to returning
		self.logger.debug('The bool value of self.waitFlag %s, is %s' % (self.waitFlag, bool(self.waitFlag)))

		
		# self.waitFlag has been converted from str to boolean via set method
		if( self.waitFlag ):
			self.logger.info(self.instance.id + ' :Waiting for Stop to complete...')

			if( self.dryRunFlag ):			
				self.logger.warning('DryRun Flag is set - waiter() will not be employed')
			else:
				# Need the Client to get the Waiter
				ec2Client=self.ec2Resource.meta.client
				waiter=ec2Client.get_waiter('instance_stopped')	

				# Waits for 40 15 second increments (e.g. up to 10 minutes)
				waiter.wait( )

		else:
			self.logger.info(self.instance.id + ' Wait for Stop to complete was not requested')
		
	def setWaitFlag(self, flag):

		# MUST convert string False to boolean False
		self.waitFlag = strtobool(flag)

	def getWaitFlag(self):
		return( self.waitFlag )
	
	def isOverrideFlagSet(self, S3BucketName, S3KeyPrefixName, overrideFileName, osType):
		''' Use SSM to check for existence of the override file in the guest OS.  If exists, don't Stop instance but log.
		Returning 'True' means the instance will not be stopped.  
			Either because the override file exists, or the instance couldn't be reached
		Returning 'False' means the instance will be stopped (e.g. not overridden)
		'''


		# If there is no overrideFilename specified, we need to return False.  This is required because the
		# remote evaluation script may evaluate to "Bypass" with a null string for the override file.  Keep in 
		# mind, DynamodDB isn't going to enforce an override filename be set in the directive.
		if not overrideFileName:
			self.logger.info(self.instance.id + ' Override Flag not set in specification.  Therefore this instance will be actioned. ')
			return False

		if not osType:
			self.logger.info(self.instance.id + ' Override Flag set BUT no Operating System attribute in specification. Therefore this instance will be actioned.')
			return False


		# Create the delegate
		ssmDelegate = SSMDelegate(self.instance.id, S3BucketName, S3KeyPrefixName, overrideFileName, osType, self.ddbRegion, self.workloadRegion)


		# Very first thing to check is whether SSM is going to write the output to the S3 bucket.  
		#   If the bucket is not in the same region as where the instances are running, then SSM doesn't write
		#   the result to the bucket and thus the rest of this method is completely meaningless to run.

		if( ssmDelegate.isS3BucketInWorkloadRegion() == SSMDelegate.S3_BUCKET_IN_CORRECT_REGION ):

			warningMsg=''
			msg=''
			# Send request via SSM, and check if send was successful
			ssmSendResult=ssmDelegate.sendSSMCommand()
			if( ssmSendResult ):
				# Have delegate advise if override file was set on instance.  If so, the instance is not to be stopped.
				overrideRes=ssmDelegate.retrieveSSMResults(ssmSendResult)
				self.logger.debug('SSMDelegate retrieveSSMResults() results :' + overrideRes)

				if( overrideRes == SSMDelegate.S3_BUCKET_IN_WRONG_REGION ):
					# Per SSM, the bucket must be in the same region as the target instance, otherwise the results will not be writte to S3 and cannot be obtained.
					self.overrideFlag=True
					warningMsg= Worker.SNS_SUBJECT_PREFIX_WARNING + ' ' + self.instance.id + ' Instance will be not be stopped because the S3 bucket is not in the same region as the workload'
					self.logger.warning(warningMsg)

				elif( overrideRes == SSMDelegate.DECISION_STOP_INSTANCE ):
					# There is a result and it specifies it is ok to Stop
					self.overrideFlag=False
					self.logger.info(self.instance.id + ' Instance will be stopped')

				elif( overrideRes == SSMDelegate.DECISION_NO_ACTION_UNEXPECTED_RESULT ):
					# Unexpected SSM Result, see log file.  Will default to overrideFlag==true out of abundance for caution
					self.overrideFlag=True
					warningMsg = Worker.SNS_SUBJECT_PREFIX_WARNING +  ' ' + self.instance.id + ' Instance will be not be stopped as there was an unexpected SSM result.'
					self.logger.warning(warningMsg)

				elif( overrideRes == SSMDelegate.DECISION_RETRIES_EXCEEDED ):
					self.overrideFlag=True
					warningMsg = Worker.SNS_SUBJECT_PREFIX_WARNING +  ' ' + self.instance.id + ' Instance will be not be stopped # retries to collect SSM result from S3 was exceeded'
					self.logger.warning(warningMsg)

				else:
					# Every other result means the instance will be bypassed (e.g. not stopped)
					self.overrideFlag=True
					msg=Worker.SNS_SUBJECT_PREFIX_INFORMATIONAL +  ' ' + self.instance.id + ' Instance will be not be stopped because override file was set'
					self.logger.info(msg)

			else:
				self.overrideFlag=True
				warningMsg=Worker.SNS_SUBJECT_PREFIX_WARNING +  ' ' + self.instance.id + ' Instance will be not be stopped because SSM could not query it'
				self.logger.warning(warningMsg)

		else:
			self.overrideFlag=True
			warningMsg=Worker.SNS_SUBJECT_PREFIX_WARNING + ' SSM will not be executed as S3 bucket is not in the same region as the workload. [' + self.instance.id + '] Instance will be not be stopped'
			self.logger.warning(warningMsg)

		if( self.snsNotConfigured == False ):
			if( self.overrideFlag == True ):
				if( warningMsg ):
					self.publishSNSTopicMessage(Worker.SNS_SUBJECT_PREFIX_WARNING, warningMsg, self.instance)
				else:
					self.publishSNSTopicMessage(Worker.SNS_SUBJECT_PREFIX_INFORMATIONAL, msg, self.instance)
		
		return( self.overrideFlag )

	def publishSNSTopicMessage(self, subjectPrefix, theMessage, instance):
		tagsMsg=''
		if( instance is not None ):
			for tag in instance.tags:
				tagsMsg = tagsMsg + '\nTag {0}=={1}'.format( str(tag['Key']), str(tag['Value']) ) 

		try:
			self.snsTopic.publish(
				Subject=self.snsTopicSubject + ':' + subjectPrefix,
				Message=theMessage + tagsMsg,
			)

		except Exception as e:
			self.logger.error('publishSNSTopicMessage() ' + str(e) )

	

	def setOverrideFlagSet(self, overrideFlag):
		self.overrideFlag=strtobool(overrideFlag)


	def execute(self, S3BucketName, S3KeyPrefixName, overrideFileName, osType):
		if( self.isOverrideFlagSet(S3BucketName, S3KeyPrefixName, overrideFileName, osType) == False ):
			self.stopInstance()

class ScalingWorker(Worker):
	def __init__(self, workloadRegion, instance, newInstanceType):
		super(ScalingWorker, self).__init__(workloadRegion, instance)
		self.newInstanceType=newInstanceType

	def modifyInstanceType(self):
		#EC2.Instance.modify_attribute()
		result=self.instance.modify_attribute(
			InstanceType={
		        'Value': self.newInstanceType
		    },
		)
		self.logger.info(self.instance.id + ' :Scaling')
		self.logger.debug(result)

	def execute(self):
		instanceState = self.instance.state
		
		if( instanceState['Name'] == 'stopped' ):
			self.modifyInstanceType()
			self.logger.debug('Instance ' + self.instance.id + 'State changed to ' + self.newInstanceType)
		else:
			logMsg = 'ScalingWorker requested to change instance type for non-stopped instance ' + self.instance.id + ' no action taken'
			self.logger.warning(logMsg)
			self.logger.debug(logMsg)


