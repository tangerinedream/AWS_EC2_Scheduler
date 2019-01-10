#!/usr/bin/env python3
import boto3
import json
import time
import datetime
import argparse
import Utils
import logging 
import re

from distutils.util import strtobool
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr
from Worker import Worker, StopWorker, StartWorker
#from Utils import RetryNotifier,SnsNotifier
from Utils import SnsNotifier
from Utils import InstanceMetaData
import getpass
from redo import retriable,retry  # See action function  https://github.com/mozilla-releng/redo
from sys import exit


__author__ = "Gary Silverman"

logger = logging.getLogger('Orchestrator') #The Module Name
auditlogger = logging.getLogger("audit_logger")

class Orchestrator(object):

	# Class Variables

	# Mapping of Python Class Variables to DynamoDB Attribute Names in Workload Table
	WORKLOAD_SPEC_TABLE_NAME='WorkloadSpecification'
	WORKLOAD_SPEC_PARTITION_KEY='SpecName'

	WORKLOAD_STATE_TABLE_NAME='WorkloadState'

	WORKLOAD_SPEC_REGION_KEY='WorkloadRegion'

	WORKLOAD_ENVIRONMENT_FILTER_TAG_KEY='WorkloadFilterTagName'
	WORKLOAD_ENVIRONMENT_FILTER_TAG_VALUE='WorkloadFilterTagValue'

	WORKLOAD_VPC_ID_KEY='VPC_ID'

	WORKLOAD_SSM_S3_BUCKET_NAME='SSMS3BucketName'
	WORKLOAD_SSM_S3_KEY_PREFIX_NAME='SSMS3KeyPrefixName'

	WORKLOAD_SNS_TOPIC_NAME='SNSTopicName'

	WORKLOAD_KILL_SWITCH="DisableAllSchedulingActions"
	WORKLOAD_KILL_SWITCH_TRUE="1"

	WORKLOAD_SCALE_INSTANCE_DELAY="ScaleInstanceDelay"  #Delay in seconds after modifyAttribute() called ahead of startIntance()


	TIER_SPEC_TABLE_NAME='TierSpecification'
	TIER_SPEC_PARTITION_KEY='SpecName'

	WORKLOAD_CROSS_ACCOUNT_ROLE='CrossAccountRole'
	ASSUME_ROLE_EXTERNAL_ID='CrossAccountRoleExternalId'

	# Mapping of Python Class Variables to DynamoDB Attribute Names in Tier Table
	TIER_FILTER_TAG_KEY='TierFilterTagName'
	TIER_FILTER_TAG_VALUE='TierTagValue'

	TIER_STOP='TierStop'
	TIER_START='TierStart'
	TIER_SCALING='TierScaling'
	TIER_NAME='TierTagValue'
	TIER_SEQ_NBR='TierSequence'
	TIER_SYCHRONIZATION='TierSynchronization'
	TIER_STOP_OVERRIDE_FILENAME='TierStopOverrideFilename'
	TIER_STOP_OS_TYPE='TierStopOverrideOperatingSystem' # Valid values are Linux and Windows

	INTER_TIER_ORCHESTRATION_DELAY='InterTierOrchestrationDelay' # The sleep time between commencing an action on this tier
	INTER_TIER_ORCHESTRATION_DELAY_DEFAULT = 5

	ACTION_STOP='Stop'
	ACTION_START='Start'
	FLEET_SUBSET='FleetSubset'
	# ACTION_SCALE_UP='ScaleUp'
	# ACTION_SCALE_DOWN='ScaleDown'

	LOG_LEVEL_INFO='info'
	LOG_LEVEL_DEBUG='debug'

	def __init__(self, partitionTargetValue, dynamoDBRegion, scalingProfile, overrideState, dryRun=False):


		# default to us-west-2
		self.dynamoDBRegion=dynamoDBRegion 
		self.workloadRegion='us-west-2'  #default

		# The commmand line param will be passed as a string
		self.dryRunFlag=dryRun
	
		self.partitionTargetValue=partitionTargetValue  # must be set prior to invoking initlogging()
	
		###
		# DynamoDB Table Related
		#
		try:
			self.dynDBC = boto3.client('dynamodb', region_name=self.dynamoDBRegion)
		except Exception as e:
			msg = 'Orchestrator::__init__() Exception obtaining botot3 dynamodb client in region %s -->' % self.workloadRegion
			logger.error(msg + str(e))

		

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
			Orchestrator.WORKLOAD_SCALE_INSTANCE_DELAY,
			Orchestrator.TIER_FILTER_TAG_KEY,
			Orchestrator.FLEET_SUBSET,
			Orchestrator.WORKLOAD_CROSS_ACCOUNT_ROLE,
		        Orchestrator.ASSUME_ROLE_EXTERNAL_ID

		]

		# Tier-specific DynamoDB Table Related
		self.tierSpecTableName=Orchestrator.TIER_SPEC_TABLE_NAME
		self.tierSpecPartitionKey=Orchestrator.TIER_SPEC_PARTITION_KEY # Same as workloadSpecificationPartitionKey

		self.workLoadState=Orchestrator.WORKLOAD_STATE_TABLE_NAME

		# Table requires the DynamoDB.Resource
		try:
			self.dynDBR = boto3.resource('dynamodb', region_name=self.dynamoDBRegion)
			self.tierSpecTable = self.dynDBR.Table(self.tierSpecTableName)
			self.WorkloadStateTable = self.dynDBR.Table(self.workLoadState)
		except Exception as e:
			msg = 'Orchestrator::__init__() Exception obtaining botot3 dynamodb resource in region %s -->' % self.workloadRegion
			logger.error(msg + str(e))

		# Create a List of valid dynamoDB attributes to address user typos in dynamoDB table
		self.tierSpecificationValidAttributeList = [
			Orchestrator.TIER_FILTER_TAG_VALUE,
			Orchestrator.TIER_SPEC_TABLE_NAME,
			Orchestrator.TIER_SPEC_PARTITION_KEY,
			Orchestrator.TIER_STOP,
			Orchestrator.TIER_START,
			Orchestrator.TIER_SCALING,
			Orchestrator.TIER_NAME,
			Orchestrator.TIER_SEQ_NBR,
			Orchestrator.TIER_SYCHRONIZATION,
			Orchestrator.TIER_STOP_OVERRIDE_FILENAME,
			Orchestrator.TIER_STOP_OS_TYPE,
			Orchestrator.INTER_TIER_ORCHESTRATION_DELAY
		]

		self.scalingProfile = scalingProfile

		self.overrideState = overrideState

		#
		###

		self.startTime=0
		self.finishTime=0

		self.workloadSpecificationDict={}

		self.tierSpecDict={}

		# Dynamically created based on tierSpecDict, based on TierSequence for Action specified
		self.sequencedTiersList=[]

		self.validActionNames = [ Orchestrator.ACTION_START, 
								  Orchestrator.ACTION_STOP
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

		# Maximum number of retries for API calls
		#self.max_api_request=8

		# Log the duration of the processing
		self.startTime = datetime.datetime.now().replace(microsecond=0)

		# Grab general workload information from DynamoDB
		self.lookupWorkloadSpecification(self.partitionTargetValue)

		# The region where the workload is running.  Note: this may be a different region than the 
		# DynamodDB configuration
		try:
			self.workloadRegion = self.workloadSpecificationDict[Orchestrator.WORKLOAD_SPEC_REGION_KEY]
		except Exception as e:
			logger.warning('Orchestrator.py::initializeState() Error obtaining self.workloadRegion --> %s' % str(e))
		# The delay (in seconds) when scaling an instance type ahead of Starting the newly scaled instance.
		# This is needed due to an eventual consistency issue in AWS whereby Instance.modifyAttribute() is changed
		# and Instance.startInstance() experiences an Exception because the modifyAttribute() has not fully propogated.
		self.scaleInstanceDelay = float(4.0)  # default to four seconds (float)
		if( Orchestrator.WORKLOAD_SCALE_INSTANCE_DELAY in self.workloadSpecificationDict ):
			try:
				delayValueStr = self.workloadSpecificationDict[Orchestrator.WORKLOAD_SCALE_INSTANCE_DELAY]
				self.scaleInstanceDelay = float(delayValueStr)
			except Exception as e:
				logger.warning('Couldn\'t convert %s to float. Using default of %s.  Exception was %s' % (delayValueStr, str(self.scaleInstanceDelay), str(e)) )

		
		# If CrossAccountRole OR CrossAccountRoleExternalId exist in DynamoDB then assume roles, otherwise use standard EC2/ELB clients:

		try:
			if ("CrossAccountRole" in self.workloadSpecificationDict):
				sts_client = boto3.client('sts')
				stsRoleArn = self.workloadSpecificationDict[Orchestrator.WORKLOAD_CROSS_ACCOUNT_ROLE]

		# As we are not enforcing ExternalID, check if it's specified and if not assign it some string
				roleExternalId = "externalID"

				if ("CrossAccountRoleExternalId" in self.workloadSpecificationDict):
					roleExternalId = self.workloadSpecificationDict[Orchestrator.ASSUME_ROLE_EXTERNAL_ID]

				ec2_assume_role = sts_client.assume_role(RoleArn=stsRoleArn,RoleSessionName="sts_assume_role",ExternalId=roleExternalId)
				sts_aws_access_key_id = ec2_assume_role['Credentials']['AccessKeyId']
				sts_aws_secret_access_key = ec2_assume_role['Credentials']['SecretAccessKey']
				sts_aws_session_token = ec2_assume_role['Credentials']['SessionToken']

				self.ec2R = boto3.resource('ec2', region_name=self.workloadRegion,aws_access_key_id=sts_aws_access_key_id,aws_secret_access_key=sts_aws_secret_access_key,aws_session_token=sts_aws_session_token)
				self.ec2_client = boto3.client('ec2',region_name=self.workloadRegion,aws_access_key_id=sts_aws_access_key_id,aws_secret_access_key=sts_aws_secret_access_key,aws_session_token=sts_aws_session_token)
				self.elb = boto3.client('elb', region_name=self.workloadRegion,aws_access_key_id=sts_aws_access_key_id,aws_secret_access_key=sts_aws_secret_access_key,aws_session_token=sts_aws_session_token)

			else:
				self.ec2R = boto3.resource('ec2', region_name=self.workloadRegion)
				self.ec2_client = boto3.client('ec2',region_name=self.workloadRegion)
				self.elb = boto3.client('elb', region_name=self.workloadRegion)

		except Exception as e:
				msg = 'Orchestrator::__init__() Exception obtaining client in region %s -->' % self.workloadRegion
				logger.error(msg + str(e))	

					
		# Grab tier specific workload information from DynamoDB
		self.lookupTierSpecs(self.partitionTargetValue)

	@retriable(attempts=5, sleeptime=0, jitter=0)
	def lookupELBs(self):
		try:
			self.all_elbs = self.elb.describe_load_balancers()
			msg = 'Orchestrator::__init() Found attached ELBs-->'
		except Exception as e:
			msg = 'Orchestrator:: Exception obtaining all ELBs in region %s --> %s' % (self.workloadRegion,e)
			subject_prefix = "Scheduler Exception in %s" % self.workloadRegion
			logger.error(msg + str(e))
			raise e

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
			logger.error('lookupWorkloadSpecification()' + e.response['Error']['Message'])
		else:
			# Get the dynamoDB Item from the result
			resultItem=dynamodbItem['Item']

		
			for attributeName in resultItem:
				# Validate the attributes entered into DynamoDB are valid.  If not, spit out individual warning messages
				if( attributeName in self.workloadSpecificationValidAttributeList ):
					attributeValue=list(resultItem[attributeName].values())[0]
					logger.info('Workload Attribute [%s maps to %s]' % (attributeName, attributeValue))
					self.workloadSpecificationDict[attributeName]=attributeValue
				else:
					logger.warning('Invalid dynamoDB attribute specified->'+str(attributeName)+'<- will be ignored')


	def recursiveFindKeys(self, sourceDict, resList):
		for k,v in sourceDict.items():
			resList.append(k)
			if( isinstance(v, dict) ):
				# Since scalingProfile key names are user dependent, we can't validate them
				if( k != Orchestrator.TIER_SCALING ):
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
			logger.error('Exception encountered in lookupTierSpecs() -->' + str(e))
		else:
			# Get the items from the result
			resultItems=dynamodbItem['Items']

			# Create a Dictionary that stores the currTier and currTier associated with Tiers
			for currTier in resultItems:
				logger.info('DynamoDB Query for Tier->'+ currTier[Orchestrator.TIER_NAME])

				tierKeys=[]
				self.recursiveFindKeys(currTier, tierKeys)
				setDiff = set(tierKeys).difference(self.tierSpecificationValidAttributeList)
				if( setDiff ):
					for badAttrKey in setDiff:
						logger.warning('Invalid dynamoDB attribute specified->'+str(badAttrKey)+'<- will be ignored')

				self.tierSpecDict[currTier[Orchestrator.TIER_NAME]] = {}
				# Pull out the Dictionaries for each of the below. 
				# Result is a key, and a dictionary
				if( Orchestrator.TIER_STOP in currTier ):
					self.tierSpecDict[ currTier[Orchestrator.TIER_NAME] ].update( { Orchestrator.TIER_STOP : currTier[ Orchestrator.TIER_STOP ] } )

				if (Orchestrator.TIER_START in currTier):
					self.tierSpecDict[ currTier[Orchestrator.TIER_NAME] ].update( { Orchestrator.TIER_START : currTier[ Orchestrator.TIER_START ] } )

				if (Orchestrator.TIER_SCALING in currTier):
					self.tierSpecDict[ currTier[Orchestrator.TIER_NAME] ].update( { Orchestrator.TIER_SCALING : currTier[ Orchestrator.TIER_SCALING ] } )

					if (Orchestrator.FLEET_SUBSET in currTier):
						self.tierSpecDict[ currtier[Orchestrator.TIER_NAME] ].update( { Orchestrator.FLEET_SUBSET : currTier[ Orchestrator.FLEET_SUBSET ] } )

				#self.logSpecDict('lookupTierSpecs', currTier, Orchestrator.LOG_LEVEL_DEBUG )

			# Log the constructed Tier Spec Dictionary
			#self.logSpecDict('lookupTierSpecs', self.tierSpecDict, Orchestrator.LOG_LEVEL_INFO )

	def sequenceTiers(self, tierAction):
		# Using the Tier Spec Dictionary, construct a simple List to order the sequence of Tier Processing
		# for the given Action.  Sequence is ascending.
		#
		# Prefill list for easy insertion
		self.sequencedTiersList=list(range( len(self.tierSpecDict)))

		# tierAction indicates whether it is a TIER_STOP, or TIER_START, as they may have different sequences
		for currKey, currAttributes in self.tierSpecDict.items():
			logger.debug('sequenceTiers() Action=%s, currKey=%s, currAttributes=%s)' % (tierAction, currKey, currAttributes) )
			
			# Grab the Tier Name first
			tierName = currKey
			#tierName = currAttributes[Orchestrator.TIER_NAME]

			tierAttributes={}	# do I need to scope this variable as such?
			logger.debug('In sequenceTiers(), tierAction is %s' % tierAction)
			if( tierAction == Orchestrator.TIER_STOP):
				# Locate the TIER_STOP Dictionary
				tierAttributes = currAttributes[Orchestrator.TIER_STOP]

			elif( tierAction == Orchestrator.TIER_START ):
				tierAttributes = currAttributes[Orchestrator.TIER_START]

			#logger.info('In sequenceTiers(): tierAttributes is ', tierAttributes )


			# Insert into the List 
			self.sequencedTiersList[ int( tierAttributes[Orchestrator.TIER_SEQ_NBR ] ) ] = tierName

		logger.debug('Sequence List for Action %s is %s' % (tierAction, self.sequencedTiersList))
			
		return( self.sequencedTiersList )
	

	def logSpecDict(self, label, dict, level):
		# for key, value in self.workloadSpecificationDict.iteritems():
		# 	logger.debug('%s (key==%s, value==%s)' % (label, key, value))
		for key, value in dict.items():
			if( level == Orchestrator.LOG_LEVEL_INFO ):
				logger.info('%s (key==%s, value==%s)' % (label, key, value))
			else:
				logger.debug('%s (key==%s, value==%s)' % (label, key, value))

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

		logger.debug('isTierSynchronized for tierName==%s, tierAction==%s is syncFlag==%s' % (tierName, tierAction, res) )
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
	
	@retriable(attempts=5,sleeptime=0, jitter=0)
	def lookupInstancesByFilter(self, targetInstanceStateKey, tierName):
	    # Use the filter() method of the instances collection to retrieve
	    # all running EC2 instances.
		logger.debug('lookupInstancesByFilter() seeking instances in tier %s' % tierName)
		logger.debug('lookupInstancesByFilter() instance state %s' % targetInstanceStateKey)
		logger.debug('lookupInstancesByFilter() tier tag key %s' % self.workloadSpecificationDict[Orchestrator.TIER_FILTER_TAG_KEY])
		logger.debug('lookupInstancesByFilter() tier tag value %s' % tierName)
		logger.debug('lookupInstancesByFilter() Env tag key %s' % self.workloadSpecificationDict[Orchestrator.WORKLOAD_ENVIRONMENT_FILTER_TAG_KEY])
		logger.debug('lookupInstancesByFilter() Env tag value %s' % self.workloadSpecificationDict[Orchestrator.WORKLOAD_ENVIRONMENT_FILTER_TAG_VALUE])


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
			logger.debug('VPC_ID provided, Filter List is %s' % str(targetFilter))

		# Filter the instances
		# NOTE: Only instances within the specified region are returned
		targetInstanceColl = {}
		try:
			targetInstanceColl = sorted(self.ec2R.instances.filter(Filters=targetFilter),key=id)
			logger.info('lookupInstancesByFilter(): # of instances found for tier %s in state %s is %i' % (tierName, targetInstanceStateKey, len(list(targetInstanceColl))))
			if(logger.level==Orchestrator.LOG_LEVEL_DEBUG):
				for curr in targetInstanceColl:
					logger.debug('lookupInstancesByFilter(): Found the following matching targets %s' % curr)
		except Exception as e:
			msg = 'Orchestrator::lookupInstancesByFilter() Exception encountered during instance filtering %s -->' % e
			logger.error(msg + str(e))
			raise e


		return targetInstanceColl

			
	def isKillSwitch(self):

		res = False
		if( Orchestrator.WORKLOAD_KILL_SWITCH in self.workloadSpecificationDict ):

			switchValue = self.workloadSpecificationDict[Orchestrator.WORKLOAD_KILL_SWITCH]

			if( switchValue == Orchestrator.WORKLOAD_KILL_SWITCH_TRUE ):
				logger.warning('Kill Switch found.  All scheduling actions on the workload will be bypassed')
				res = True

		return( res )

	
	def orchestrate(self, action ):
		'''
		Given an Action, 
		1) Iterate through the Tiers based on the sequence and
		2) Apply the directive to each tier, applying the inter-tier delay factor 
		3) Log
		'''


		killSwitch = self.isKillSwitch()

		if( killSwitch ):
			bodyMsg = '%s: Kill Switch Enabled.   All scheduling actions on the workload will be bypassed.' % self.workloadSpecificationDict[Orchestrator.WORKLOAD_ENVIRONMENT_FILTER_TAG_VALUE]
			snsTopicSubjectLine = "Scheduler Kill Switch enabled"
			self.snsInit.sendSns(snsTopicSubjectLine, bodyMsg)

		else:

			if( action == Orchestrator.ACTION_STOP ):

				# Sequence the tiers per the STOP order
				self.sequenceTiers(Orchestrator.TIER_STOP)

				for currTier in self.sequencedTiersList:
				
					logger.info('Orchestrate() Stopping Tier: ' + currTier)
				
					# Stop the next tier in the sequence
					self.stopATier(currTier)
	
				# Update DDB WorkloadState only if DryRun is False:
				if (self.dryRunFlag == False):
					self.updateWorkloadStateTable(action)				

			elif( action == Orchestrator.ACTION_START ):

				doStartFlag = True
				if (self.overrideState):
					logger.info('Orchestrate() Override -o flag detected, not checking WorkloadState DynamoDB table')
				else:
					# Check State TableCheck if Env is already started
					envStatus = orchMain.readWorkloadStateTable()

					# If started, no reason to try to start it again
					if (envStatus == Orchestrator.ACTION_START):
						logger.info(
							'Orchestrate() Workload marked as Started in DynamoDB, not performing Start action. Please use -o flag to override this if required. Workload: ' + self.partitionTargetValue)
						doStartFlag = False

				if(doStartFlag):
					# Start the workload
					try:
						orchMain.lookupELBs()
					except Exception as e:
						self.sns.sendSns("orchMain.lookupELBs() has encountered an exception ", str(e)) # See action function  https://github.com/mozilla-releng/redo
					# Sequence the tiers per the START order
					self.sequenceTiers(Orchestrator.TIER_START)
					for currTier in self.sequencedTiersList:
						logger.info('Orchestrate() Starting Tier: ' + currTier)
						# Start the next tier in the sequence
						self.startATier(currTier)
					# Update DDB WorkloadState only if DryRun is False
					if (self.dryRunFlag == False):
						self.updateWorkloadStateTable(action)

			elif( action not in self.validActionNames ):
				
				logger.warning('Action requested %s not a valid choice of ', self.validActionNames)
			
			else:
			
				logger.warning('Action requested %s is not yet implemented. No action taken', action)	

		# capture completion time
		self.finishTime = datetime.datetime.now().replace(microsecond=0)

		logger.info('++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++')
		logger.info('++ Completed processing ['+ action +'][' + self.partitionTargetValue + ']<- in ' + str(self.finishTime - self.startTime) + ' seconds')
		logger.info('++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++')
	

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
		try:
			instancesToStopList = self.lookupInstancesByFilter(running,tierName)
		except Exception as e:
			self.sns.sendSns("Orchestrator::lookupInstancesByFilter() has encountered an exception", str(e))

		

		# Determine if operations on the Tier should be synchronized or not
		tierSynchronized=self.isTierSynchronized(tierName, Orchestrator.TIER_STOP)

		# Grab the EC2 region (not DynamodDB region) for the worker to make API calls
		#region=self.workloadSpecificationDict[self.WORKLOAD_SPEC_REGION_KEY]


		for currInstance in instancesToStopList:
			stopWorker = StopWorker(self.dynamoDBRegion, self.workloadRegion, currInstance, self.dryRunFlag,self.ec2_client, self.sns)
			stopWorker.setWaitFlag(tierSynchronized)
			
			if all (k in self.workloadSpecificationDict for k in (Orchestrator.WORKLOAD_SSM_S3_BUCKET_NAME,Orchestrator.WORKLOAD_SSM_S3_KEY_PREFIX_NAME)):
				ssmS3Bucket = self.workloadSpecificationDict[Orchestrator.WORKLOAD_SSM_S3_BUCKET_NAME],
				ssmS3KeyPrefixName = self.workloadSpecificationDict[Orchestrator.WORKLOAD_SSM_S3_KEY_PREFIX_NAME],
			else:
				ssmS3Bucket = ""
				ssmS3KeyPrefixName = ""

			stopWorker.execute(
				ssmS3Bucket,
				ssmS3KeyPrefixName,
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

		# Find the stopped instances of this tier to start
		try:
			stoppedInstancesList = self.lookupInstancesByFilter(stopped,tierName)
		except Exception as e:
			self.sns.sendSns("Orchestrator::lookupInstancesByFilter() has encountered an exception",str(e))


		running=self.instanceStateMap[16]
		try:
			runningInstancesList = self.lookupInstancesByFilter(running,tierName)
		except Exception as e:
			self.sns.sendSns("Orchestrator::lookupInstancesByFilter() has encountered an exception",str(e))



		totalInstancesList = stoppedInstancesList + runningInstancesList

		# Determine if operations on the Tier should be synchronized or not,
		# currently this feature is not implemented for Starting A Tier
		#syncFlag=self.isTierSynchronized(tierName, Orchestrator.TIER_START)

		# Grab the region the worker should make API calls against
		#region=self.workloadSpecificationDict[self.WORKLOAD_SPEC_REGION_KEY]

		logger.debug('In startATier() for %s', tierName)


		# numberOfInstances is the output of isFleetSubset method

		targetInstanceCount = self.calculateInstanceNumber(tierName, totalInstancesList)
		self.startInstances(targetInstanceCount, runningInstancesList, stoppedInstancesList, tierName)
	

	def startInstances(self, targetInstanceCount, runningInstancesList, stoppedInstancesList, tierName):
		self.targetInstanceCount = targetInstanceCount
		self.runningInstancesList = runningInstancesList
		self.stoppedInstancesList = stoppedInstancesList
		self.allInstances = runningInstancesList + stoppedInstancesList
		self.tierName = tierName

		self.alreadyRunning = len(runningInstancesList)
		self.toStart = int(self.targetInstanceCount) - len(self.runningInstancesList)
		self.startList = self.stoppedInstancesList[:self.toStart]

		# Start and change required instances 
		# If instanceTypeToLaunch is present, it means Profile is specified
		
		instanceTypeToLaunch = self.isScalingAction(self.tierName)
		for currInstance in self.allInstances:
			startWorker = StartWorker(self.dynamoDBRegion, self.workloadRegion, currInstance, self.all_elbs, self.elb,self.scaleInstanceDelay, self.dryRunFlag,self.ec2_client, self.sns)
			if (instanceTypeToLaunch):
				startWorker.scaleInstance(instanceTypeToLaunch)
				logger.info('Changed all EC2 instance types to %s in Tier %s' % (instanceTypeToLaunch,self.tierName))
			if currInstance in self.startList:
				logger.debug('Starting instance %s', currInstance)
				startWorker.start()

		# Delay to be introduced prior to allowing the next tier to be actioned.
		# It may make sense to allow some amount of time for the instances to Stop, prior to Orchestration continuing.

		time.sleep(self.getInterTierOrchestrationDelay(self.tierName, Orchestrator.TIER_START))
		logger.debug('startATier() completed for tier %s' % self.tierName)

	def calculateInstanceNumber(self, tierName, totalInstancesList):

#	We asume that there is no profile specified and that all instances will be started
		numberOfInstances = len(totalInstancesList)
		if(self.scalingProfile):
			logger.debug('scalingProfile requested - FleetSubset calculator')
			
			# Unpack the ScalingDictionary
			tierAttributes = self.tierSpecDict[tierName]
			if( Orchestrator.TIER_SCALING in tierAttributes):

				scalingDict = tierAttributes[ Orchestrator.TIER_SCALING ]
				logger.debug('FleetSubset for Tier %s %s ' % (tierName, str(scalingDict) ))

				if ( self.scalingProfile in scalingDict ):
					if Orchestrator.FLEET_SUBSET in scalingDict[self.scalingProfile]:
						fleetNumber = scalingDict[self.scalingProfile]['FleetSubset']
						if re.search("%", fleetNumber):
							splitNumberOfInstances = fleetNumber.split("%")[0]
							if (int(splitNumberOfInstances) <= 0) or (int(splitNumberOfInstances) > 100):
								logger.info('FleetSubset specified out of range (less than 0i%% or more than 100%%) for profile [%s] and Tier [%s], starting all EC2 instances ' % (str(self.scalingProfile), tierName ) )
							else:
				                                numberOfInstances = int(round(int(splitNumberOfInstances) * len(list(totalInstancesList)) / 100.0))
						else:
							numberOfInstances = fleetNumber
						
				else:
					logger.warning('FleetSubset of [%s] not in tier [%s], will start all EC2 instances within a Tier ' % (str(self.scalingProfile), tierName ) )
			else:
				logger.warning('FleetSubset Profile of [%s] specified but no FleetSubset dictionary found in DynamoDB for tier [%s]. Starting all EC2 instancew within a Tier' % (str(self.scalingProfile), tierName) )

		return numberOfInstances
	
	def isScalingAction(self, tierName):

		# First, is the ScalingProfile flag even set ?
		if(self.scalingProfile):
			logger.debug('ScalingProfile requested')

			# Unpack the ScalingDictionary
			tierAttributes = self.tierSpecDict[tierName]
			if( Orchestrator.TIER_SCALING in tierAttributes):

				scalingDict = tierAttributes[ Orchestrator.TIER_SCALING ]
				logger.debug('ScalingProfile for Tier %s is %s ' % (tierName, str(scalingDict) ))

				# Ok, so next, does this tier have a Scaling Profile?
				if( self.scalingProfile in scalingDict ):
					# Ok, so then what is the EC2 InstanceType to launch with, for the given ScalingProfile specified ?
					try:
						instanceType = scalingDict[self.scalingProfile]['InstanceType']
						return instanceType
					except:
					# This is to introduce backward compatibility with previous DynamoDB scheme, without profile map, and also to allow that InstanceType is not specified at all
						try:
							instanceType = scalingDict[self.scalingProfile]
							if type(instanceType) == dict:
								logger.warning('ScalingProfile InstanceType could not be found, starting all the instances within Tier %s' % tierName)
							else:	
								logger.info('instanceType: %s' % instanceType)
								return instanceType
						except:
							logger.warning('Scaling Profile of [%s] not in tier [%s] ' % (str(self.scalingProfile), tierName ) )
			
				else:
					logger.warning('Scaling Profile of [%s] not in tier [%s] ' % (str(self.scalingProfile), tierName ) )
			else:
				logger.warning('Scaling Profile of [%s] specified but no TierScaling dictionary found in DynamoDB for tier [%s].  No scaling action taken' % (str(self.scalingProfile), tierName) )

		return( None )

	def runTestCases(self):
		logger.info('Executing runTestCases()')
		
		self.initializeState()

		# print 'Role_Web override file loc ', self.getTierStopOverrideFilename('Role_Web')
		# print 'Role_AppServer override file loc ', self.getTierStopOverrideFilename('Role_AppServer')
		# print 'Role_DB override file loc ', self.getTierStopOverrideFilename('Role_DB')
		# Test Case: Stop an Environment
		logger.info('\n### Orchestrating START Action ###')
		self.orchestrate(Orchestrator.ACTION_START )

		# Test Case: Start an Environment
		sleepSecs=20
		logger.info('\n### Sleeping for ' + str(sleepSecs) + ' seconds ###')
		time.sleep(sleepSecs)

		logger.info('\n### Orchestrating STOP Action ###')
		self.orchestrate(Orchestrator.ACTION_STOP )


	def snsInit(self):
		sns_topic_name = self.workloadSpecificationDict[Orchestrator.WORKLOAD_SNS_TOPIC_NAME]
		sns_workload	= self.workloadSpecificationDict[Orchestrator.WORKLOAD_SPEC_PARTITION_KEY]
		self.sns	= SnsNotifier(sns_topic_name,sns_workload)

	@retriable(attempts=5, sleeptime=0, jitter=0)
	def readWorkloadStateTable(self):
		self.currentTime = str(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
	# ConditionExpression will make that item is put into DDB only if attribute Workload doesn't exist
		try:
				response = self.WorkloadStateTable.put_item(
				Item={
				'Workload': self.partitionTargetValue,
				'LastActionTime': str(self.currentTime),
				'LastActionType': 'Unmanaged',
				},
				ConditionExpression = "attribute_not_exists(Workload)")   
				logger.info("Updated WorkloadState DynamoDB")
		except Exception as e:
			if e.response['Error']['Code'] != 'ConditionalCheckFailedException':
				msg = 'Orchestrator::readWorkloadStateTable() Exception encountered during DDB put_item %s -->' % e 
				logger.error(msg + str(e))
				raise e

		try:
			dynamodbWorkloadState = self.WorkloadStateTable.query(
				KeyConditionExpression=Key('Workload').eq(self.partitionTargetValue),
				ConsistentRead=False,
				ReturnConsumedCapacity="TOTAL",
			)
		except ClientError as e:
			logger.error('Exception encountered in readWorkloadStateTable() for query -->' + str(e))
					
		return dynamodbWorkloadState['Items'][0]['LastActionType']

	def updateWorkloadStateTable(self,action):

		self.currentTime = str(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

		if (action == Orchestrator.ACTION_START) and (self.scalingProfile is not None):
			UpdateExpressionAttr='SET Profile= :profile, LastActionTime= :currentTime, LastActionType= :actionType'
			ExpressionAttributeValuesAttr={
					':profile': self.scalingProfile,
					':currentTime': self.currentTime,
					':actionType': action
    					}

		else:
			UpdateExpressionAttr='SET LastActionTime= :currentTime, LastActionType= :actionType'
			ExpressionAttributeValuesAttr={
					':currentTime': self.currentTime,
					':actionType': action,
    					}
	
		try:
			retry(self.WorkloadStateTable.update_item, attempts=5, sleeptime=0,jitter=0, kwargs= {
    				"Key":{
				        'Workload': self.partitionTargetValue,
				    },
				    "UpdateExpression":UpdateExpressionAttr,
				    "ExpressionAttributeValues":ExpressionAttributeValuesAttr
				})
		except Exception as e:
			msg = 'Orchestrator::updateWorkloadStateTable() Exception encountered during DDB update %s -->' % e
			logger.error(msg + str(e))	

if __name__ == "__main__":
	# python Orchestrator.py -i workloadIdentier -r us-west-2

	parser = argparse.ArgumentParser(description='Command line parser')
	parser.add_argument('-w','--workloadIdentifier', help='Workload Identifier to Action Upon',required=True)
	parser.add_argument('-r','--dynamoDBRegion', help='Region where the DynamoDB configuration exists. Note: could be different from the target EC2 workload is running', required=True)
	parser.add_argument('-a','--action', choices=['Stop', 'Start'], help='Action to Orchestrate (e.g. Stop or Start)', required=False)
	parser.add_argument('-t','--testcases', action='count', help='Run the test cases', required=False)
	parser.add_argument('-d','--dryrun', action='count', help='Run but take no Action', required=False)
	parser.add_argument('-p','--scalingProfile', help='Resize instances based on Scaling Profile name', required=False)
	parser.add_argument('-l','--loglevel', choices=['critical', 'error', 'warning', 'info', 'debug', 'notset'], help='The level to record log messages to the logfile', required=False)
	parser.add_argument('-o','--overrideState', action='count',help='Override WorkloadState table', required=False)

# This is happening because in Python3 it can't compare NoneType and Int, so I've changed it to check if it's NoneType	
	args = parser.parse_args()
	
	if( args.loglevel is not None ):
		loglevel = args.loglevel
	else:
		loglevel = 'info'

	if( args.dryrun is not None ):
		dryRun = True
	else:
		dryRun = False


	if( args.overrideState is not None ):
		overrideState = True
	else:
		overrideState = False

	# Launch the Orchestrator - the main component of the subsystem
	LogStreamName = "NonEC2Instance"
	NameTag = ""
	Creds = ""
	MetaDataError = ""
	try:
		InstanceID = InstanceMetaData().getInstanceID()
		LogStreamName = InstanceMetaData().getInstanceEnvTag(InstanceID)
		NameTag = InstanceMetaData().getInstanceNameTag(InstanceID)
		Creds = InstanceMetaData().getCredentials()
	except Exception as e:
		MetaDataError = str(e)
	Utils.initLogging(args.loglevel,args.workloadIdentifier,LogStreamName)

	if MetaDataError:
		logger.error(MetaDataError)
	auditlogger.info({'UserName': getpass.getuser(), 'Profile': args.scalingProfile or '', 'Workload': args.workloadIdentifier,'Action': args.action, 'Hostname': NameTag,'AccessKey': Creds,'EnvironmentName': LogStreamName,}) #Logs this Dict to Audit stream in CW
	orchMain = Orchestrator(args.workloadIdentifier, args.dynamoDBRegion, args.scalingProfile, overrideState, dryRun)
	# If testcases set, run them, otherwise run the supplied Action only
	if( args.testcases is not None ):	
		orchMain.runTestCases()
	else:

		# Default Action to Start
		if( args.action ):
			action = args.action
		else:
			action = Orchestrator.ACTION_START

		logger.info('\n### Orchestrating %s' % action +' Action ###')
		orchMain.initializeState()
		orchMain.snsInit()
		orchMain.orchestrate(action)
