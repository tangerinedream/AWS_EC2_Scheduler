#!/usr/bin/python
import boto3
import json
import logging
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr
import Worker

class Orchestrator(object):

	# Class Variables
	SPEC_REGION_KEY='Region'

	ENVIRONMENT_FILTER_TAG_KEY='EnvFilterTagName'
	ENVIRONMENT_FILTER_TAG_VALUE='EnvFilterTagValue'

	TIER_FILTER_TAG_KEY='TierFilterTagName'
	TIER_FILTER_TAG_VALUE='TierTagValue'

	TIER_STOP='TierStop'
	TIER_START='TierStart'
	TIER_NAME='TierTagValue'
	TIER_SEQ_NBR='TierSequence'
	TIER_SYCHRONIZATION='TierSynchronization'
	TIER_STOP_OVERRIDE_FILENAME='TierStopOverrideFilename'

	ACTION_STOP='Stop'
	ACTION_START='Start'
	ACTION_SCALE_UP='ScaleUp'
	ACTION_SCALE_DOWN='ScaleDown'

	def __init__(self, partitionTargetValue, action, region='us-west-2'):
		self.region=region 					# default to us-west-2
		self.interTierOrchestrationDelay= 0 # number of seconds to delay inbetween tier orchestration
		
		###
		# DynamoDB Table Related
		#
		self.dynDBC = boto3.client('dynamodb', region_name=self.region)

		# Directive DynamoDB Table Related
		self.directiveSpecTableName='DirectiveSpec'
		self.directiveSpecPartitionKey='SpecName'
		self.partitionTargetValue=partitionTargetValue

		# Tier-specific DynamoDB Table Related
		self.tierSpecTableName='TierSpecification'
		self.tierSpecPartitionKey='SpecName'  # Same as directiveSpecPartitionKey

		# Table requires the DynamoDB.Resource
		self.dynDBR = boto3.resource('dynamodb', region_name=self.region)
		self.tierSpecTable = self.dynDBR.Table(self.tierSpecTableName)
		#
		###

		###
		# EC2 Resouce
		self.ec2R = boto3.resource('ec2', region_name=self.region)
		#
		###

		
		###
		# Python / Boto Datastructures
		#
		# {
		#	"SpecName" : "BotoTestCase1",
		#	"Region" : "us-west-2",
		#	"EnvFilterTagName" : "Environment",
		#	"EnvFilterTagValue" : "ENV001",
		#	"TierFilterTagName" : "Role",
		# }
		#
		self.directiveSpecDict={}

		# {
		#   "SpecName": "BotoTestCase1",
		#   "TierTagValue": "Role_AppServer"
		#   "TierStart": {
		#     "TierSequence": "1",
		#     "TierSynchronization": "False"
		#   },
		#   "TierStop": {
		#     "TierSequence": "1",
		#     "TierStopOverrideFilename": "/tmp/StopOverride",
		#     "TierSynchronization": "True"
		#   },
		# }
		self.tierSpecDict={}

		# Dynamically created based on tierSpecDict, based on TierSequence for Action specified
		self.sequencedTiersList=[]

		# Actions : e.g. what the request is supposed to do to the instances/tiers
		self.action=action
		self.validActionNames = [ Orchestrator.ACTION_START, 
								  Orchestrator.ACTION_STOP, 
								  Orchestrator.ACTION_SCALE_UP,
								  Orchestrator.ACTION_SCALE_DOWN 
								]

		self.instanceStateMap = {
			0: "pending",
			16: "running",
			32: "shutting-down",
			48: "terminated",
			64: "stopping",
			80: "stopped"
		}

		self.initLogging()

	def initializeState(self, action):
		if( self.action not in self.validActionNames ):
			self.logger.error('Action requested %s not a valid choice of ', self.validActionNames)
			quit()

		# Grab general workload information from DynamoDB
		self.lookupDirectiveSpec(self.partitionTargetValue)

		# Grab tier specific workload information from DynamoDB
		self.lookupTierSpecs(self.partitionTargetValue)

		# Establish the tier sequence for the requested action
		if( self.action == Orchestrator.ACTION_STOP):
			self.sequenceTiers(Orchestrator.TIER_STOP)
		elif( self.action == Orchestrator.ACTION_START):
			self.sequenceTiers(Orchestrator.TIER_START)

	def lookupDirectiveSpec(self, partitionTargetValue):
		try:
			dynamodbItem=self.dynDBC.get_item(
				TableName=self.directiveSpecTableName,
				Key={
					self.directiveSpecPartitionKey : { "S" : partitionTargetValue }
				},
				ConsistentRead=False,
				ReturnConsumedCapacity="TOTAL",
			)
		except ClientError as e:
			self.logger.warning(e.response['Error']['Message'])
		else:
			# Get the item from the result
			resultItem=dynamodbItem['Item']
			
			for attributeName in resultItem:
				#print "AttributeName: ", attributeName
				attributeValue=resultItem[attributeName].values()[0]
				#print "AttributeValue: ", attributeValue + '\n'

				self.directiveSpecDict[attributeName]=attributeValue

			for key, value in self.directiveSpecDict.iteritems():
				print 'directiveSpecDict (key=%s, value=%s)' % (key, value)
			#
			#self.processItemJSON(resultItem)
			#

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
			#print dynamodbItem
		except ClientError as e:
			self.logger.warning(e.response['Error']['Message'])
		else:
			# Get the items from the result
			resultItems=dynamodbItem['Items']
			self.logger.debug(resultItems)

			# Create a Dictionary that stores the attributes and attributeValues associated with Tiers
			for attribute in resultItems:
				# print "Tier Tag Value ==>", attribute['TierTagValue']
				# print "TIER_START==>", attribute[Orchestrator.TIER_START]
				# print '\n'
				# print "TIER_STOP==>", attribute[Orchestrator.TIER_STOP]
				# print '\n\n'
				self.tierSpecDict[attribute['TierTagValue']]={Orchestrator.TIER_STOP : attribute[Orchestrator.TIER_STOP], Orchestrator.TIER_START : attribute[Orchestrator.TIER_START]}

			# Log the constructed Tier Spec Dictionary
			for key, value in self.tierSpecDict.iteritems():
				self.logger.debug('tierSpecDict (key=%s, value=%s)' % (key, value))

	def sequenceTiers(self, tierAction):
		# Using the Tier Spec Dictionary, construct a simple List to order the sequence of Tier Processing
		# for the given Action.  Sequence is ascending.
		#
		# tierAction indicates whether it is a TIER_STOP, or TIER_START, as they may have different sequences
		for currKey, currAttributes in self.tierSpecDict.iteritems():
			self.logger.debug('sequenceList Action=%s, currKey=%s, currAttributes=%s)' % (tierAction, currKey, currAttributes) )
			
			# Grab the Tier Name first
			tierName = currKey
			#tierName = currAttributes[Orchestrator.TIER_NAME]

			tierAttributes={}	# do I need to scope this variable as such?
			if( tierAction == Orchestrator.TIER_STOP):
				# Locate the TIER_STOP Dictionary
				tierAttributes = currAttributes[Orchestrator.TIER_STOP]

			elif( tierAction == Orchestrator.TIER_START ):
				tierAttributes = currAttributes[Orchestrator.TIER_START]

				# Insert into the List at the index specified as the sequence number in the Dict 
			self.sequencedTiersList.insert( int(tierAttributes[Orchestrator.TIER_SEQ_NBR]) , tierName)

		self.logger.debug('Sequence List for Action %s is %s' % (tierAction, self.sequencedTiersList))
			
		return( self.sequencedTiersList )
	

	def printSpecDict(self):
		for key, value in self.directiveSpecDict.iteritems():
			print 'directiveSpecDict (key=%s, value=%s)' % (key, value)

	def isTierSynchronized(self, tierName, tierAction):
		# Get the Tier Named tierName
		tierAttributes = self.tierSpecDict[tierName]

		# Get the dictionary for the correct Action
		tierActionAttribtes={}
		if( tierAction == Orchestrator.TIER_STOP):
			# Locate the TIER_STOP Dictionary
			tierActionAttributes = tierAttributes[Orchestrator.TIER_STOP]

		elif( tierAction == Orchestrator.TIER_START ):
			# Locate the TIER_START Dictionary
			tierActionAttributes = tierAttributes[Orchestrator.TIER_START]

		# Return the value in the Dict for TierSynchronization
		if Orchestrator.TIER_SEQ_NBR in tierActionAttributes:
			res = tierActionAttributes[Orchestrator.TIER_SEQ_NBR]
		else:
			res = False

		self.logger.debug('isTierSynchronized for %s, %s is %s' % (tierName, tierAction, res) )
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
	
	def lookupInstancesByFilter(self, targetInstanceStateKey, tierName):
	    # Use the filter() method of the instances collection to retrieve
	    # all running EC2 instances.
		self.logger.debug('In lookupInstancesByFilter() seeking instances in tier %s' % tierName)
		self.printSpecDict()
		self.logger.debug('  instance state %s' % targetInstanceStateKey)
		self.logger.debug('  tier tag key %s' % self.directiveSpecDict[Orchestrator.TIER_FILTER_TAG_KEY])
		self.logger.debug('  tier tag value %s' % tierName)
		self.logger.debug('  Env tag key %s' % self.directiveSpecDict[Orchestrator.ENVIRONMENT_FILTER_TAG_KEY])
		self.logger.debug('  Env tag value %s' % self.directiveSpecDict[Orchestrator.ENVIRONMENT_FILTER_TAG_VALUE])


		targetFilter = [
			{
		        'Name': 'instance-state-name', 
		        'Values': [targetInstanceStateKey]
		    },
		    {
		        'Name': 'tag:' + self.directiveSpecDict[Orchestrator.ENVIRONMENT_FILTER_TAG_KEY],
		        'Values': [self.directiveSpecDict[Orchestrator.ENVIRONMENT_FILTER_TAG_VALUE]]
		    },
		    {
		        'Name': 'tag:' + self.directiveSpecDict[Orchestrator.TIER_FILTER_TAG_KEY],
		        'Values': [tierName]
		    }
		]
		# targetFilter = [
		# 	{
		#         'Name': 'instance-state-name', 
		#         'Values': [targetInstanceStateKey]
		#     },
		#     {
		#         'Name': 'tag:' + Orchestrator.ENVIRONMENT_FILTER_TAG_KEY,
		#         'Values': [self.directiveSpecDict[Orchestrator.ENVIRONMENT_FILTER_TAG_KEY]]
		#     },
		#     {
		#         'Name': 'tag:' + Orchestrator.ENVIRONMENT_FILTER_TAG_VALUE,
		#         'Values': [self.directiveSpecDict[Orchestrator.ENVIRONMENT_FILTER_TAG_VALUE]]
		#     },
		#     {
		#         'Name': 'tag:' + Orchestrator.TIER_FILTER_TAG_KEY,
		#         'Values': [self.directiveSpecDict[Orchestrator.TIER_FILTER_TAG_KEY]]
		#     },
		#     {
		#         'Name': 'tag:' + Orchestrator.TIER_FILTER_TAG_VALUE,
		#         'Values': [tierName]
		#     }
		# ]

		#filter the instances
		# NOTE: Only instances within the specified region are returned

		targetInstanceColl = self.ec2R.instances.filter(Filters=targetFilter)

		#if( len(targetInstanceColl) > 0 ) :
		#for item in targetInstanceColl:
		#	self.logger.debug('Target instance found :', item)
		# else:
		# 	self.logger.info('In lookupInstancesByFilter() no instances found based on filter')

		return targetInstanceColl
	
	def orchestrate(self, specName, action ):
		'''
		Given a SpecName, and an Action, 
		1) Iterate through the Tiers based on the sequence and
		2) Apply the directive to each tier, applying the inter-tier delay factor 
		3) Log
		'''

		if( action == Orchestrator.ACTION_STOP ):
			for currTier in self.sequencedTiersList:
				self.stopATier(currTier)
		elif( action == Orchestrator.ACTION_START ): 
			for currTier in self.sequencedTiersList:
				self.startATier(currTier)


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
		running=self.instanceStateMap[16]

		# Find the instances
		instancesToStopList = self.lookupInstancesByFilter(
			running, 
			tierName
		)

		region=self.directiveSpecDict[self.SPEC_REGION_KEY]
		syncFlag=self.isTierSynchronized(tierName, Orchestrator.TIER_STOP)

		# if( len(instancesToStopList) >  0 ) :
		# 	for currInstance in instancesToStopList:
		# 		self.logger.debug('Stopping instance %s', currInstance)
		# 		stopWorker = StopWorker(region, currInstance, syncFlag)
		# 		stopWorker.execute()
		# else :
		# 	self.logger.debug('No instances found to stop with state=%s, tagKey=%s, tagValue=%s' % (running, targetTagName, targetTagValue) )
		for currInstance in instancesToStopList:
			self.logger.debug('Stopping instance %s', currInstance)
			stopWorker = StopWorker(region, currInstance, syncFlag)
			stopWorker.execute()

		self.logger.debug('stopATier() completed for tier %s' % tierName)

	def postEvent(self):
		# If SNS flag enabled and SNS setup, also send to SNS
		pass

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
		if( self.isTierSynchronized(tierName, orch.TIER_START) ):
			pass
		pass




	def scaleInstance(self, direction):
		pass

	def setInterTierOrchestrationDelay(self, seconds):
		pass

	def initLogging(self):
		# Setup the Logger
		self.logger = logging.getLogger("Orchestrator")  #The Module Name
		logging.basicConfig(format='%(asctime)s:%(levelname)s:%(name)s==>%(message)s\n', filename="Orchestrator" + '.log', level=logging.DEBUG)
		
		# Setup the Handlers
		# create console handler and set level to debug
		consoleHandler = logging.StreamHandler()
		consoleHandler.setLevel(logging.INFO)
		self.logger.addHandler(consoleHandler)

	def runTestCases(self):
		self.logger.info("Executing initializeState()")
		self.initializeState(Orchestrator.ACTION_STOP)
		print 'Role_Web override file loc ', self.getTierStopOverrideFilename('Role_Web')
		print 'Role_AppServer override file loc ', self.getTierStopOverrideFilename('Role_AppServer')
		print 'Role_DB override file loc ', self.getTierStopOverrideFilename('Role_DB')
		self.orchestrate('BotoTestCase1', Orchestrator.ACTION_STOP )


if __name__ == "__main__":
	orchMain = Orchestrator('BotoTestCase1', Orchestrator.ACTION_STOP, 'us-west-2')
	orchMain.runTestCases()
	

