#!/usr/bin/python
import boto3
import json
import logging
import logging.handlers
import time
import datetime
import argparse

from distutils.util import strtobool
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr
from Worker import Worker, StopWorker, StartWorker

__author__ = "Gary Silverman"

class Orchestrator(object):

	# Class Variables
	WORKLOAD_SPEC_TABLE_NAME='WorkloadSpecification'
	WORKLOAD_SPEC_PARTITION_KEY='SpecName'

	WORKLOAD_SPEC_REGION_KEY='WorkloadRegion'

	WORKLOAD_ENVIRONMENT_FILTER_TAG_KEY='WorkloadFilterTagName'
	WORKLOAD_ENVIRONMENT_FILTER_TAG_VALUE='WorkloadFilterTagValue'

	WORKLOAD_VPC_ID_KEY='VPC_ID'

	WORKLOAD_SSM_S3_BUCKET_NAME='SSMS3BucketName'
	WORKLOAD_SSM_S3_KEY_PREFIX_NAME='SSMS3KeyPrefixName'

	WORKLOAD_SNS_TOPIC_NAME='SNSTopicName'

	WORKLOAD_KILL_SWITCH="DisableAllSchedulingActions"
	WORKLOAD_KILL_SWITCH_TRUE="1"



	TIER_FILTER_TAG_KEY='TierFilterTagName'
	TIER_FILTER_TAG_VALUE='TierTagValue'

	TIER_SPEC_TABLE_NAME='TierSpecification'
	TIER_SPEC_PARTITION_KEY='SpecName'

	TIER_STOP='TierStop'
	TIER_START='TierStart'
	TIER_NAME='TierTagValue'
	TIER_SEQ_NBR='TierSequence'
	TIER_SYCHRONIZATION='TierSynchronization'
	TIER_STOP_OVERRIDE_FILENAME='TierStopOverrideFilename'
	TIER_STOP_OS_TYPE='TierStopOverrideOperatingSystem' # Valid values are Linux and Windows

	INTER_TIER_ORCHESTRATION_DELAY='InterTierOrchestrationDelay' # The sleep time between commencing an action on this tier
	INTER_TIER_ORCHESTRATION_DELAY_DEFAULT = 5

	ACTION_STOP='Stop'
	ACTION_START='Start'
	ACTION_SCALE_UP='ScaleUp'
	ACTION_SCALE_DOWN='ScaleDown'

	LOG_LEVEL_INFO='info'
	LOG_LEVEL_DEBUG='debug'

	def __init__(self, partitionTargetValue, loglevel, dynamoDBRegion, dryRun=False):

		self.partitionTargetValue=partitionTargetValue  # must be set prior to invoking initlogging()
		self.initLogging(loglevel)

		# default to us-west-2
		self.dynamoDBRegion=dynamoDBRegion 
		self.workloadRegion='us-west-2'  #default

		# The commmand line param will be passed as a string
		self.dryRunFlag=dryRun
		
		###
		# DynamoDB Table Related
		#
		try:
			self.dynDBC = boto3.client('dynamodb', region_name=self.dynamoDBRegion)
		except Exception as e:
			msg = 'Orchestrator::__init__() Exception obtaining botot3 dynamodb client in region %s -->' % self.workloadRegion
			self.logger.error(msg + str(e))


		# Directive DynamoDB Table Related
		self.workloadSpecificationTableName=Orchestrator.WORKLOAD_SPEC_TABLE_NAME
		self.workloadSpecificationPartitionKey=Orchestrator.WORKLOAD_SPEC_PARTITION_KEY


		# Create a List of valid dynamoDB attributes to address user typos in dynamoDB table
		self.workloadSpecificationValidAttributeList = [
			Orchestrator.WORKLOAD_SPEC_PARTITION_KEY,
			Orchestrator.WORKLOAD_SPEC_REGION_KEY,
			Orchestrator.WORKLOAD_ENVIRONMENT_FILTER_TAG_KEY,
			Orchestrator.WORKLOAD_ENVIRONMENT_FILTER_TAG_VALUE,
			Orchestrator.WORKLOAD_VPC_ID_KEY,
			Orchestrator.WORKLOAD_SSM_S3_BUCKET_NAME,
			Orchestrator.WORKLOAD_SSM_S3_KEY_PREFIX_NAME,
			Orchestrator.WORKLOAD_SNS_TOPIC_NAME,
			Orchestrator.WORKLOAD_KILL_SWITCH,
			Orchestrator.TIER_FILTER_TAG_KEY
		]

		# Tier-specific DynamoDB Table Related
		self.tierSpecTableName=Orchestrator.TIER_SPEC_TABLE_NAME
		self.tierSpecPartitionKey=Orchestrator.TIER_SPEC_PARTITION_KEY # Same as workloadSpecificationPartitionKey

		# Table requires the DynamoDB.Resource
		try:
			self.dynDBR = boto3.resource('dynamodb', region_name=self.dynamoDBRegion)
			self.tierSpecTable = self.dynDBR.Table(self.tierSpecTableName)
		except Exception as e:
			msg = 'Orchestrator::__init__() Exception obtaining botot3 dynamodb resource in region %s -->' % self.workloadRegion
			self.logger.error(msg + str(e))

		# Create a List of valid dynamoDB attributes to address user typos in dynamoDB table
		self.tierSpecificationValidAttributeList = [
			Orchestrator.TIER_FILTER_TAG_VALUE,
			Orchestrator.TIER_SPEC_TABLE_NAME,
			Orchestrator.TIER_SPEC_PARTITION_KEY,
			Orchestrator.TIER_STOP,
			Orchestrator.TIER_START,
			Orchestrator.TIER_NAME,
			Orchestrator.TIER_SEQ_NBR,
			Orchestrator.TIER_SYCHRONIZATION,
			Orchestrator.TIER_STOP_OVERRIDE_FILENAME,
			Orchestrator.TIER_STOP_OS_TYPE,
			Orchestrator.INTER_TIER_ORCHESTRATION_DELAY
		]

		#
		###

		# Get the SNS Topic
		self.snsTopicR = boto3.resource('sns', region_name=dynamoDBRegion)
		self.snsTopic = ''
		self.snsNotConfigured=False

		self.startTime=0
		self.finishTime=0

		self.workloadSpecificationDict={}

		self.tierSpecDict={}

		# Dynamically created based on tierSpecDict, based on TierSequence for Action specified
		self.sequencedTiersList=[]

		self.validActionNames = [ Orchestrator.ACTION_START, 
								  Orchestrator.ACTION_STOP, 
								  Orchestrator.ACTION_SCALE_UP,
								  Orchestrator.ACTION_SCALE_DOWN 
								]

		# These are the official codes per Boto3
		self.instanceStateMap = {
			0: "pending",
			16: "running",
			32: "shutting-down",
			48: "terminated",
			64: "stopping",
			80: "stopped"
		}



	def initializeState(self):

		# Log the duration of the processing
		self.startTime = datetime.datetime.now().replace(microsecond=0)

		# Grab general workload information from DynamoDB
		self.lookupWorkloadSpecification(self.partitionTargetValue)

		# The region where the workload is running.  Note: this may be a different region than the 
		# DynamodDB configuration
		self.workloadRegion=self.workloadSpecificationDict[Orchestrator.WORKLOAD_SPEC_REGION_KEY]

		# We provision the boto3 resource here because we need to have determined the Workload Region as a dependency,
		# which is done just above in this method
		try:
			self.ec2R = boto3.resource('ec2', region_name=self.workloadRegion)
		except Exception as e:
			msg = 'Orchestrator::initializeState() Exception obtaining botot3 ec2 resource in region %s -->' % self.workloadRegion
			self.logger.error(msg + str(e))

		# Grab tier specific workload information from DynamoDB
		self.lookupTierSpecs(self.partitionTargetValue)


	def lookupWorkloadSpecification(self, partitionTargetValue):
		try:
			dynamodbItem=self.dynDBC.get_item(
				TableName=self.workloadSpecificationTableName,
				Key={
					self.workloadSpecificationPartitionKey : { "S" : partitionTargetValue }
				},
				ConsistentRead=False,
				ReturnConsumedCapacity="TOTAL",
			)
		except ClientError as e:
			self.logger.error('lookupWorkloadSpecification()' + e.response['Error']['Message'])
		else:
			# Get the dynamoDB Item from the result
			resultItem=dynamodbItem['Item']

		
			for attributeName in resultItem:
				# Validate the attributes entered into DynamoDB are valid.  If not, spit out individual warning messages
				if( attributeName in self.workloadSpecificationValidAttributeList ):
					attributeValue=resultItem[attributeName].values()[0]
					self.logger.info('Workload Attribute [%s maps to %s]' % (attributeName, attributeValue))
					self.workloadSpecificationDict[attributeName]=attributeValue
				else:
					self.logger.warning('Invalid dynamoDB attribute specified->'+str(attributeName)+'<- will be ignored')


	def recursiveFindKeys(self, sourceDict, resList):
		for k,v in sourceDict.iteritems():
			resList.append(k)
			if( isinstance(v, dict) ):
				self.recursiveFindKeys(v, resList)

	def lookupTierSpecs(self, partitionTargetValue):
		'''
		Find all rows in table with partitionTargetValue
		Build a Dictionary (of Dictionaries).  Dictionary Keys are: TIER_START, TIER_STOP, TierScaleUp, TierScaleDown
			Values are attributeValues of the DDB Item Keys
		'''
		try:
			dynamodbItem=self.tierSpecTable.query(
				KeyConditionExpression=Key(self.tierSpecPartitionKey).eq(partitionTargetValue),
				ConsistentRead=False,
				ReturnConsumedCapacity="TOTAL",
			)
		except ClientError as e:
			self.logger.error('Exception encountered in lookupTierSpecs() -->' + str(e))
		else:
			# Get the items from the result
			resultItems=dynamodbItem['Items']

			# Create a Dictionary that stores the currTier and currTier associated with Tiers
			for currTier in resultItems:
				self.logger.info('DynamoDB Query for Tier->'+ currTier[Orchestrator.TIER_NAME])

				tierKeys=[]
				self.recursiveFindKeys(currTier, tierKeys)
				setDiff = set(tierKeys).difference(self.tierSpecificationValidAttributeList)
				if( setDiff ):
					for badAttrKey in setDiff:
						self.logger.warning('Invalid dynamoDB attribute specified->'+str(badAttrKey)+'<- will be ignored')

				# Pull out the Dictionaries for each of the below. 
				# Result is a key, and a dictionary
				self.tierSpecDict[ currTier[Orchestrator.TIER_NAME] ] = {
					Orchestrator.TIER_STOP : currTier[ Orchestrator.TIER_STOP ], 
					Orchestrator.TIER_START : currTier[ Orchestrator.TIER_START ]
				}
				#self.logSpecDict('lookupTierSpecs', currTier, Orchestrator.LOG_LEVEL_DEBUG )

			# Log the constructed Tier Spec Dictionary
			#self.logSpecDict('lookupTierSpecs', self.tierSpecDict, Orchestrator.LOG_LEVEL_INFO )

	def sequenceTiers(self, tierAction):
		# Using the Tier Spec Dictionary, construct a simple List to order the sequence of Tier Processing
		# for the given Action.  Sequence is ascending.
		#
		# Prefill list for easy insertion
		self.sequencedTiersList=range( len(self.tierSpecDict) )

		# tierAction indicates whether it is a TIER_STOP, or TIER_START, as they may have different sequences
		for currKey, currAttributes in self.tierSpecDict.iteritems():
			self.logger.debug('sequenceTiers() Action=%s, currKey=%s, currAttributes=%s)' % (tierAction, currKey, currAttributes) )
			
			# Grab the Tier Name first
			tierName = currKey
			#tierName = currAttributes[Orchestrator.TIER_NAME]

			tierAttributes={}	# do I need to scope this variable as such?
			self.logger.debug('In sequenceTiers(), tierAction is %s' % tierAction)
			if( tierAction == Orchestrator.TIER_STOP):
				# Locate the TIER_STOP Dictionary
				tierAttributes = currAttributes[Orchestrator.TIER_STOP]

			elif( tierAction == Orchestrator.TIER_START ):
				tierAttributes = currAttributes[Orchestrator.TIER_START]

			#self.logger.info('In sequenceTiers(): tierAttributes is ', tierAttributes )


			# Insert into the List 
			self.sequencedTiersList[ int( tierAttributes[Orchestrator.TIER_SEQ_NBR ] ) ] = tierName

		self.logger.debug('Sequence List for Action %s is %s' % (tierAction, self.sequencedTiersList))
			
		return( self.sequencedTiersList )
	

	def logSpecDict(self, label, dict, level):
		# for key, value in self.workloadSpecificationDict.iteritems():
		# 	self.logger.debug('%s (key==%s, value==%s)' % (label, key, value))
		for key, value in dict.iteritems():
			if( level == Orchestrator.LOG_LEVEL_INFO ):
				self.logger.info('%s (key==%s, value==%s)' % (label, key, value))
			else:
				self.logger.debug('%s (key==%s, value==%s)' % (label, key, value))

	def isTierSynchronized(self, tierName, tierAction):
		# Get the Tier Named tierName
		tierAttributes = self.tierSpecDict[tierName]

		# Get the dictionary for the correct Action
		tierActionAttributes={}
		if( tierAction == Orchestrator.TIER_STOP):
			# Locate the TIER_STOP Dictionary
			tierActionAttributes = tierAttributes[Orchestrator.TIER_STOP]

		elif( tierAction == Orchestrator.TIER_START ):
			# Locate the TIER_START Dictionary
			tierActionAttributes = tierAttributes[Orchestrator.TIER_START]
			#print tierActionAttributes

		# Return the value in the Dict for TierSynchronization
		if Orchestrator.TIER_SYCHRONIZATION in tierActionAttributes:
			res = tierActionAttributes[Orchestrator.TIER_SYCHRONIZATION]
		else:
			res = False

		self.logger.debug('isTierSynchronized for tierName==%s, tierAction==%s is syncFlag==%s' % (tierName, tierAction, res) )
		return( res )

	def getTierStopOverrideFilename(self, tierName):

		# Get the Tier Named tierName
		tierAttributes = self.tierSpecDict[tierName]

		# Get the dictionary for the correct Action
		tierActionAttribtes={}

		# Locate the TIER_STOP Dictionary method only applies to TIER_STOP
		tierActionAttributes = tierAttributes[Orchestrator.TIER_STOP]
		
		# Return the value in the Dict for TierStopOverrideFilename
		if Orchestrator.TIER_STOP_OVERRIDE_FILENAME in tierActionAttributes:
			res = tierActionAttributes[Orchestrator.TIER_STOP_OVERRIDE_FILENAME]
		else:
			res = ''

		return( res )

	def getTierOperatingSystemType(self, tierName):

		# Get the Tier Named tierName
		tierAttributes = self.tierSpecDict[tierName]

		# Get the dictionary for the correct Action
		tierActionAttribtes={}

		# Locate the TIER_STOP Dictionary
		tierActionAttributes = tierAttributes[Orchestrator.TIER_STOP]
		
		# Return the value in the Dict for TierStopOverrideOperatingSystem
		if Orchestrator.TIER_STOP_OS_TYPE in tierActionAttributes:
			res = tierActionAttributes[Orchestrator.TIER_STOP_OS_TYPE]
		else:
			res = ''

		return( res )

	def getInterTierOrchestrationDelay(self, tierName, tierAction):

		# Get the Tier Named tierName
		tierAttributes = self.tierSpecDict[tierName]

		# Get the dictionary for the correct Action
		tierActionAttribtes={}

		# Locate the TIER_STOP Dictionary
		tierActionAttributes = tierAttributes[tierAction]
		
		# Return the value in the Dict for TierStopOverrideOperatingSystem
		if Orchestrator.INTER_TIER_ORCHESTRATION_DELAY in tierActionAttributes:
			res = tierActionAttributes[Orchestrator.INTER_TIER_ORCHESTRATION_DELAY]
		else:
			res = Orchestrator.INTER_TIER_ORCHESTRATION_DELAY_DEFAULT

		return( float(res) )
	
	def lookupInstancesByFilter(self, targetInstanceStateKey, tierName):
	    # Use the filter() method of the instances collection to retrieve
	    # all running EC2 instances.
		self.logger.debug('lookupInstancesByFilter() seeking instances in tier %s' % tierName)
		self.logger.debug('lookupInstancesByFilter() instance state %s' % targetInstanceStateKey)
		self.logger.debug('lookupInstancesByFilter() tier tag key %s' % self.workloadSpecificationDict[Orchestrator.TIER_FILTER_TAG_KEY])
		self.logger.debug('lookupInstancesByFilter() tier tag value %s' % tierName)
		self.logger.debug('lookupInstancesByFilter() Env tag key %s' % self.workloadSpecificationDict[Orchestrator.WORKLOAD_ENVIRONMENT_FILTER_TAG_KEY])
		self.logger.debug('lookupInstancesByFilter() Env tag value %s' % self.workloadSpecificationDict[Orchestrator.WORKLOAD_ENVIRONMENT_FILTER_TAG_VALUE])


		targetFilter = [
			{
		        'Name': 'instance-state-name', 
		        'Values': [targetInstanceStateKey]
		    },
		    {
		        'Name': 'tag:' + self.workloadSpecificationDict[Orchestrator.WORKLOAD_ENVIRONMENT_FILTER_TAG_KEY],
		        'Values': [self.workloadSpecificationDict[Orchestrator.WORKLOAD_ENVIRONMENT_FILTER_TAG_VALUE]]
		    },
		    {
		        'Name': 'tag:' + self.workloadSpecificationDict[Orchestrator.TIER_FILTER_TAG_KEY],
		        'Values': [tierName]
		    }
		]

		# If the Optional VPC ID was provided to further tighten the filter, include it.
		if( Orchestrator.WORKLOAD_VPC_ID_KEY in self.workloadSpecificationDict ):
			vpc_filter_dict_element = { 
				'Name': 'vpc-id', 
		        'Values': [self.workloadSpecificationDict[Orchestrator.WORKLOAD_VPC_ID_KEY]]
			}
			targetFilter.append(vpc_filter_dict_element)
			self.logger.debug('VPC_ID provided, Filter List is %s' % str(targetFilter))

		# Filter the instances
		# NOTE: Only instances within the specified region are returned
		targetInstanceColl = {}
		try:
			targetInstanceColl = self.ec2R.instances.filter(Filters=targetFilter)

			self.logger.info('lookupInstancesByFilter(): # of instances found for tier %s in state %s is %i' % (tierName, targetInstanceStateKey, len(list(targetInstanceColl))))
			for curr in targetInstanceColl:
				self.logger.debug('lookupInstancesByFilter(): Found the following matching targets %s' % curr)

		except Exception as e:
			msg = 'Orchestrator::lookupInstancesByFilter() Exception encountered during instance filtering %s -->'
			self.logger.error(msg + str(e))


		return targetInstanceColl

	def makeSNSTopic(self):

		if (self.workloadSpecificationDict[Orchestrator.WORKLOAD_SNS_TOPIC_NAME]):

			# Make or retrieve the SNS Topic setup.  Method is Idempotent
			try:
				self.snsTopic = self.snsTopicR.create_topic( Name=self.workloadSpecificationDict[Orchestrator.WORKLOAD_SNS_TOPIC_NAME] )
				self.snsTopicSubjectLine = self.makeSNSTopicSubjectLine()

			except Exception as e:
				self.logger.error('orchestrate() - creating SNS Topic ' + str(e) )
				self.snsNotConfigured=True
		else:
			self.snsNotConfigured=True

	def publishSNSTopic(self, subject, message):
		try:
			self.snsTopic.publish(
				Subject=subject,
				Message=message,
			)

		except Exception as e:
			self.logger.error('publishSNSTopicMessage() ' + str(e) )

	def isKillSwitch(self):

		res = False
		if( Orchestrator.WORKLOAD_KILL_SWITCH in self.workloadSpecificationDict ):

			switchValue = self.workloadSpecificationDict[Orchestrator.WORKLOAD_KILL_SWITCH]

			if( switchValue == Orchestrator.WORKLOAD_KILL_SWITCH_TRUE ):
				self.logger.warning('Kill Switch found.  All scheduling actions on the workload will be bypassed')
				res = True

		return( res )

	
	def orchestrate(self, action ):
		'''
		Given an Action, 
		1) Iterate through the Tiers based on the sequence and
		2) Apply the directive to each tier, applying the inter-tier delay factor 
		3) Log
		'''

		self.makeSNSTopic()

		killSwitch = self.isKillSwitch()

		if( killSwitch ):
			bodyMsg = '%s: Kill Switch Enabled.   All scheduling actions on the workload will be bypassed.' % self.workloadSpecificationDict[Orchestrator.WORKLOAD_ENVIRONMENT_FILTER_TAG_VALUE]
			self.publishSNSTopic(self.snsTopicSubjectLine, bodyMsg)

		else:

			if( action == Orchestrator.ACTION_STOP ):

				# Sequence the tiers per the STOP order
				self.sequenceTiers(Orchestrator.TIER_STOP)

				for currTier in self.sequencedTiersList:
				
					self.logger.info('Orchestrate() Stopping Tier: ' + currTier)
				
					# Stop the next tier in the sequence
					self.stopATier(currTier)
					

			elif( action == Orchestrator.ACTION_START ): 
				
				# Sequence the tiers per the START order
				self.sequenceTiers(Orchestrator.TIER_START)
				
				for currTier in self.sequencedTiersList:
				
					self.logger.info('Orchestrate() Starting Tier: ' + currTier)
				
					# Start the next tier in the sequence
					self.startATier(currTier)

			elif( action not in self.validActionNames ):
				
				self.logger.warning('Action requested %s not a valid choice of ', self.validActionNames)
			
			else:
			
				self.logger.warning('Action requested %s is not yet implemented. No action taken', action)	

		# capture completion time
		self.finishTime = datetime.datetime.now().replace(microsecond=0)

		self.logger.info('++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++')
		self.logger.info('++ Completed processing for workload ->' + self.partitionTargetValue +'<- in ' + str(self.finishTime - self.startTime) + ' seconds')
		self.logger.info('++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++')
	
	def makeSNSTopicSubjectLine(self):
		res = 'AWS_EC2_Scheduler Notification:  Workload==>' + self.workloadSpecificationDict[Orchestrator.WORKLOAD_SPEC_PARTITION_KEY]
		return( res )	    

	def stopATier(self, tierName):
		'''
		Given a Tier,
		0) We may want to create a separate "client" per instance within the tier, if we process in parallel
		1) Determine if the override flag is set, and if so, log and bypass
		2) Determine if the tier is synchronized and if so, ensure the use Waiters is applied
		   during processing, prior to returning
		3) Stop the tier 
		4) Log 
		'''
		
		# Find the running instances of this tier to stop
		running=self.instanceStateMap[16]

		instancesToStopList = self.lookupInstancesByFilter(
			running,
			tierName
		)

		# Determine if operations on the Tier should be synchronized or not
		tierSynchronized=self.isTierSynchronized(tierName, Orchestrator.TIER_STOP)

		# Grab the EC2 region (not DynamodDB region) for the worker to make API calls
		#region=self.workloadSpecificationDict[self.WORKLOAD_SPEC_REGION_KEY]

		for currInstance in instancesToStopList:
			stopWorker = StopWorker(self.dynamoDBRegion, self.workloadRegion, self.snsNotConfigured, self.snsTopic, self.snsTopicSubjectLine, currInstance, self.logger, self.dryRunFlag)
			stopWorker.setWaitFlag(tierSynchronized)
			stopWorker.execute(
				self.workloadSpecificationDict[Orchestrator.WORKLOAD_SSM_S3_BUCKET_NAME],
				self.workloadSpecificationDict[Orchestrator.WORKLOAD_SSM_S3_KEY_PREFIX_NAME],
				self.getTierStopOverrideFilename(tierName),
				self.getTierOperatingSystemType(tierName)
			)

		# Configured delay to be introduced prior to allowing the next tier to be Actioned.
		# It may make sense to allow some amount of time for the instances to Stop, prior to Orchestration continuing.
		time.sleep(self.getInterTierOrchestrationDelay(tierName, Orchestrator.TIER_STOP))

	def startATier(self, tierName):
		'''
		Given a Tier,
		0) We may want to create a separate "client" per instance within the tier, if we process in parallel
		1) Determine if the override flag is set, and if so, log and bypass
		2) Determine if the tier is synchronized and if so, ensure the use Waiters is applied
		   during processing, prior to returning
		3) Start the tier 
		4) Log 
		'''

		stopped=self.instanceStateMap[80]

		# Find the running instances of this tier to stop
		instancesToStartList = self.lookupInstancesByFilter(
			stopped,
			tierName
		)

		# Determine if operations on the Tier should be synchronized or not,
		# currently this feature is not implemented for Starting A Tier
		#syncFlag=self.isTierSynchronized(tierName, Orchestrator.TIER_START)

		# Grab the region the worker should make API calls against
		#region=self.workloadSpecificationDict[self.WORKLOAD_SPEC_REGION_KEY]

		self.logger.debug('In startATier() for %s', tierName)
		for currInstance in instancesToStartList:
			self.logger.debug('Starting instance %s', currInstance)
			startWorker = StartWorker(self.dynamoDBRegion, self.workloadRegion, self.snsNotConfigured, self.snsTopic, self.snsTopicSubjectLine, currInstance, self.logger, self.dryRunFlag)
			startWorker.execute()

		# Delay to be introduced prior to allowing the next tier to be actioned.
		# It may make sense to allow some amount of time for the instances to Stop, prior to Orchestration continuing.
		time.sleep(self.getInterTierOrchestrationDelay(tierName, Orchestrator.TIER_START))

		self.logger.debug('startATier() completed for tier %s' % tierName)

	def postEvent(self):
		# If SNS flag enabled and SNS setup, also send to SNS
		pass

	def scaleInstance(self, direction):
		pass

	def initLogging(self, loglevel):
		# Setup the Logger
		self.logger = logging.getLogger('Orchestrator')  #The Module Name

		# Set logging level
		loggingLevelSelected = logging.INFO

		# Set logging level
		if( loglevel == 'critical' ):
			loggingLevelSelected=logging.CRITICAL
		elif( loglevel == 'error' ):
			loggingLevelSelected=logging.ERROR
		elif( loglevel == 'warning' ):
			loggingLevelSelected=logging.WARNING
		elif( loglevel == 'info' ):
			loggingLevelSelected=logging.INFO
		elif( loglevel == 'debug' ):
			loggingLevelSelected=logging.DEBUG
		elif( loglevel == 'notset' ):
			loggingLevelSelected=logging.NOTSET

		filenameVal='Orchestrator_' + self.partitionTargetValue + '.log' 

		log_formatter = logging.Formatter('[%(asctime)s][%(levelname)s][%(module)s:%(funcName)s()][%(lineno)d]%(message)s')

		# Add the rotating file handler
		handler = logging.handlers.RotatingFileHandler(
			filename=filenameVal,
			mode='a',
			maxBytes=128 * 1024,
			backupCount=10)
		handler.setFormatter(log_formatter)

		self.logger.addHandler(handler)
		self.logger.setLevel(loggingLevelSelected)

	def runTestCases(self):
		self.logger.info('Executing runTestCases()')
		
		self.initializeState()

		# print 'Role_Web override file loc ', self.getTierStopOverrideFilename('Role_Web')
		# print 'Role_AppServer override file loc ', self.getTierStopOverrideFilename('Role_AppServer')
		# print 'Role_DB override file loc ', self.getTierStopOverrideFilename('Role_DB')
		# Test Case: Stop an Environment
		self.logger.info('\n### Orchestrating START Action ###')
		self.orchestrate(Orchestrator.ACTION_START )

		# Test Case: Start an Environment
		sleepSecs=20
		self.logger.info('\n### Sleeping for ' + str(sleepSecs) + ' seconds ###')
		time.sleep(sleepSecs)

		self.logger.info('\n### Orchestrating STOP Action ###')
		self.orchestrate(Orchestrator.ACTION_STOP )


if __name__ == "__main__":
	# python Orchestrator.py -i workloadIdentier -r us-west-2

	parser = argparse.ArgumentParser(description='Command line parser')
	parser.add_argument('-w','--workloadIdentifier', help='Workload Identifier to Action Upon',required=True)
	parser.add_argument('-r','--dynamoDBRegion', help='Region where the DynamoDB configuration exists. Note: could be different from the target EC2 workload is running', required=True)
	parser.add_argument('-a','--action', choices=['Stop', 'Start'], help='Action to Orchestrate (e.g. Stop or Start)', required=False)
	parser.add_argument('-t','--testcases', action='count', help='Run the test cases', required=False)
	parser.add_argument('-d','--dryrun', action='count', help='Run but take no Action', required=False)
	parser.add_argument('-l','--loglevel', choices=['critical', 'error', 'warning', 'info', 'debug', 'notset'], help='The level to record log messages to the logfile', required=False)
	
	args = parser.parse_args()

	if( args.loglevel > 0 ):
		loglevel = args.loglevel
	else:
		loglevel = 'info'

	if( args.dryrun > 0 ):
		dryRun = True
	else:
		dryRun = False

	# Launch the Orchestrator - the main component of the subsystem
	orchMain = Orchestrator(args.workloadIdentifier, loglevel, args.dynamoDBRegion, dryRun)

	# If testcases set, run them, otherwise run the supplied Action only
	if( args.testcases > 0 ):	
		orchMain.runTestCases()
	else:

		# Default Action to Start
		if( args.action ):
			action = args.action
		else:
			action = Orchestrator.ACTION_START

		orchMain.logger.info('\n### Orchestrating %s' % action +' Action ###')
		orchMain.initializeState()
		orchMain.orchestrate(action)

	

