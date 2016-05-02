#!/usr/bin/python
import boto3
import json
import logging
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr

class Orchestrator(object):

	def __init__(self, partitionTargetValue, action, region='us-west-2'):
		self.region=region 					# default to us-west-2
		self.interTierOrchestrationDelay= 0 # number of seconds to delay inbetween tier orchestration
		
		self.directiveSpecTableName='DirectiveSpec'
		self.directiveSpecPartitionKey='SpecName'
		self.partitionTargetValue=partitionTargetValue


		self.action=action
		self.ACTION_STOP='Stop'
		self.ACTION_START='Start'
		self.ACTION_SCALE_UP="ScaleUp"
		self.ACTION_SCALE_DOWN="ScaleDown"
		self.validActionNames = [self.ACTION_START, self.ACTION_STOP, self.ACTION_SCALE_UP, self.ACTION_SCALE_DOWN]
		
		self.directiveSpecDict={}
		self.dynDBC = boto3.client('dynamodb', region_name=self.region)

		self.tierSpecTableName='TierSpecification'
		self.tierSpecPartitionKey='SpecName'  # Same as directiveSpecPartitionKey
		self.tierSpecDict={}
		self.sequencedList=[]

		self.TIER_STOP='TierStop'
		self.TIER_START='TierStart'
		self.TIER_NAME='TierTagValue'
		self.TIER_SEQ_NBR='TierSequence'
		self.TIER_SYCHRONIZATION='TierSynchronization'
		self.TIER_STOP_OVERRIDE_FILENAME='TierStopOverrideFilename'

		# Table requires the DynamoDB.Resource
		self.dynDBR = boto3.resource('dynamodb', region_name=self.region)
		self.tierSpecTable = self.dynDBR.Table(self.tierSpecTableName)


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
		if( self.action == self.ACTION_STOP):
			self.sequenceTiers(self.TIER_STOP)
		elif( self.action == self.ACTION_START):
			self.sequenceTiers(self.TIER_START)

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
				# print "TIER_START==>", attribute[self.TIER_START]
				# print '\n'
				# print "TIER_STOP==>", attribute[self.TIER_STOP]
				# print '\n\n'
				self.tierSpecDict[attribute['TierTagValue']]={self.TIER_STOP : attribute[self.TIER_STOP], self.TIER_START : attribute[self.TIER_START]}

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
			#tierName = currAttributes[self.TIER_NAME]

			tierAttributes={}	# do I need to scope this variable as such?
			if( tierAction == self.TIER_STOP):
				# Locate the TIER_STOP Dictionary
				tierAttributes = currAttributes[self.TIER_STOP]

			elif( tierAction == self.TIER_START ):
				tierAttributes = currAttributes[self.TIER_START]

				# Insert into the List at the index specified as the sequence number in the Dict 
			self.sequencedList.insert( int(tierAttributes[self.TIER_SEQ_NBR]) , tierName)

		self.logger.debug('Sequence List for Action %s is %s' % (tierAction, self.sequencedList))
			
		return( self.sequencedList )
	

	def isTierSynchronized(self, tierName, tierAction):
		# Get the Tier Named tierName
		tierAttributes = self.tierSpecDict[tierName]

		# Get the dictionary for the correct Action
		tierActionAttribtes={}
		if( tierAction == self.TIER_STOP):
			# Locate the TIER_STOP Dictionary
			tierActionAttributes = tierAttributes[self.TIER_STOP]

		elif( tierAction == self.TIER_START ):
			# Locate the TIER_START Dictionary
			tierActionAttributes = tierAttributes[self.TIER_START]

		# Return the value in the Dict for TierSynchronization
		if self.TIER_SYCHRONIZATION in tierActionAttributes:
			res = tierActionAttributes[self.TIER_SYCHRONIZATION]
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
		tierActionAttributes = tierAttributes[self.TIER_STOP]
		
		# Return the value in the Dict for TierStopOverrideFilename
		if self.TIER_STOP_OVERRIDE_FILENAME in tierActionAttributes:
			res = tierActionAttributes[self.TIER_STOP_OVERRIDE_FILENAME]
		else:
			res = ''

		return( res )
	

	def orchestrate(self):
		'''
		Given a Spec, and a Directive, 
		1) Determine the right Sequencing for applying the directive to the tierSpecDict
		2) Iterate through the Tiers based on the sequence and
		3) Apply the directive to each tier, applying the inter-tier delay factor 
		4) Log
		'''
		pass


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
		if( self.isTierSynchronized(tierName, orch.TIER_STOP) ):
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
		self.initializeState(self.ACTION_STOP)
		print 'Role_Web override file loc ', self.getTierStopOverrideFilename('Role_Web')
		print 'Role_AppServer override file loc ', self.getTierStopOverrideFilename('Role_AppServer')
		print 'Role_DB override file loc ', self.getTierStopOverrideFilename('Role_DB')


if __name__ == "__main__":
	orchMain = Orchestrator('BotoTestCase1', 'Stop', 'us-west-2')
	orchMain.runTestCases()
	

