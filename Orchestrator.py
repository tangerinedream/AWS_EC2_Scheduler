#!/usr/bin/python
import boto3
import json
import logging
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr

class Orchestrator(object):

	def __init__(self, region='us-west-2'):
		self.region=region 					# default to us-west-2
		self.directiveSpecTableName='DirectiveSpec'
		self.directiveSpecPartitionKey='SpecName'

		self.tierSpecTableName='TierSpecification'
		self.tierSpecPartitionKey='SpecName'  # Same as directiveSpecPartitionKey
		self.specDict={}
		self.dynDBC = boto3.client('dynamodb', region_name=self.region)
		self.initLogging()

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

				self.specDict[attributeName]=attributeValue

			for key, value in self.specDict.iteritems():
				print 'specDict (key=%s, value=%s)' % (key, value)
			#
			#self.processItemJSON(resultItem)
			#

	def lookupTierSpec(self, partitionTargetValue):
		'''
		Find all rows in table with partitionTargetValue
		Build a Dictionary (of Dictionaries).  Dictionary Keys are: TierStart, TierStop, TierScaleUp, TierScaleDown
			Values are attributeValues of the DDB Item Keys
		'''
		try:
			dynamodb = boto3.resource('dynamodb', region_name='us-west-2')
			specTable = dynamodb.Table(self.tierSpecTableName)

			dynamodbItem=specTable.query(
				KeyConditionExpression=Key('SpecName').eq('BotoTestCase1'),
				ConsistentRead=False,
				ReturnConsumedCapacity="TOTAL",
			)

			print dynamodbItem
		except ClientError as e:
			self.logger.warning(e.response['Error']['Message'])
		else:
			# Get the item from the result
			resultItems=dynamodbItem['Items']

			for i in resultItems:
				print "Tier Tag Value ==>", i['TierTagValue']
				print "TierStart==>", i['TierStart']
				print '\n'
				print "TierStop==>", i['TierStop']
				print '\n\n'

			'''
			for attributeName in resultItems[0]:
				print "AttributeName: ", attributeName
				attributeValue=resultItems[attributeName].values()[0]
				print "AttributeValue: ", attributeValue + '\n'

			for attributeName in resultItems[1]:
				print "AttributeName: ", attributeName
				attributeValue=resultItems[attributeName].values()[0]
				print "AttributeValue: ", attributeValue + '\n'

			for attributeName in resultItems[2]:
				print "AttributeName: ", attributeName
				attributeValue=resultItems[attributeName].values()[0]
				print "AttributeValue: ", attributeValue + '\n'
			'''

	def processItemJSON(self, resultItem):
		#print "Result Item Raw: ", resultItem

		# Convert DynamoDB output to readable JSON
		resultItemAsJSON=json.dumps(resultItem, sort_keys=True, indent=4, separators=(',',':'))
		self.logger.info("Result Item as JSON==> " + resultItemAsJSON)
		self.logger.debug("Result Item as JSON==> " + resultItemAsJSON)
		
		# Convert to Python List
		resultItemAsList=json.loads(resultItemAsJSON)
		#print "Result Item as List: ", resultItemAsList
		

		# Convert into new SpecDictionary
		for attributeName in resultItemAsList:
			# print("AttributeName: ")
			# print(attributeName)
			attributeValue=resultItemAsList[attributeName].values()[0]
			# print("AttributeValue: ")
			# print(attributeValue)
			# print('\n')
			self.specDict[attributeName]=attributeValue

		'''
		Set the Standard Specs for convenience
			SpecName	: the Partition Key indicating the "name" of the specification 
			Directive 	: the action to be taken, "Start", "Stop", "Scale"
			Region 		: the region to execute the action against
			EnvFilterTagName 	: The Tag Name which will contain the Environment identifier
			EnvFilterTagValue 	: The Tag Value which contains the Environment Name to filter against to locate instances
			TierFilterTagName	: The Tag Name which will contain the Tier identifier
			TierSequence<X> : the identifier indicating the Tag Value of the tier, and its sequence position for action  
		'''
		self.specName=self.specDict['SpecName']
		logging.debug("SpecName==>" + self.specName)
		self.specRegion=self.specDict['Region']
		logging.debug("SpecRegion==>" + self.specRegion)
		self.envFilterTagName=self.specDict['EnvFilterTagName']
		logging.debug("EnvFilterTagName==>" + self.envFilterTagName)
		self.envFilterTagValue=self.specDict['EnvFilterTagValue']
		logging.debug("EnvFilterTagValue==>" + self.envFilterTagValue)

		'''
		Now, create an ordered list of Tier Sequences, using a Dictionary, and capture whether 

		For each item in the dictionary
			check if "TierSequence" in $instances
				YES: 
					parse the item to get the sequence number (e.g. "3" if "TierSequence3")
					PUT seq# : item into TierSequenceList NOTE: insertion order into the list doesn't matter
					list.insert(index, item) 
					This will give you an ordered list of tiers to action upon


		'''


		# # Use to debug the created SpecDict
		# for key, value in self.specDict.iteritems():
		# 	print ('SpecDict key')
		# 	print key
		# 	print ('SpecDict value')
		# 	print self.specDict[key]
		# 	print('\n')


	def sequenceTiers(self):
		pass


	def orchestrate(self):
		pass


	def postEvent(self):
		# If SNS flag enabled and SNS setup, also send to SNS
		pass

	def startTier(self):
		pass


	def stopTier(self):
		pass


	def scaleInstance(self):
		pass

	def initLogging(self):
		# Setup the Logger
		self.logger = logging.getLogger("Orchestrator")  #The Module Name
		logging.basicConfig(format='%(asctime)s:%(levelname)s:%(name)s==>%(message)s', filename="Orchestrator" + '.log', level=logging.DEBUG)
		
		# Setup the Handlers
		# create console handler and set level to debug
		consoleHandler = logging.StreamHandler()
		consoleHandler.setLevel(logging.INFO)
		self.logger.addHandler(consoleHandler)

if __name__ == "__main__":
	orch = Orchestrator('us-west-2')
	#orch.lookupDirectiveSpec('BotoTestCase1')
	orch.lookupTierSpec('BotoTestCase1')
	

