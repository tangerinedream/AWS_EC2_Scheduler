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
		self.directiveSpecDict={}
		self.dynDBC = boto3.client('dynamodb', region_name=self.region)
		


		self.tierSpecTableName='TierSpecification'
		self.tierSpecPartitionKey='SpecName'  # Same as directiveSpecPartitionKey
		self.tierSpecDict={}

		# Table requires the DynamoDB.Resource
		self.dynDBR = boto3.resource('dynamodb', region_name=self.region)
		self.tierSpecTable = self.dynDBR.Table(self.tierSpecTableName)

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

				self.directiveSpecDict[attributeName]=attributeValue

			for key, value in self.directiveSpecDict.iteritems():
				print 'directiveSpecDict (key=%s, value=%s)' % (key, value)
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
			dynamodbItem=self.tierSpecTable.query(
				KeyConditionExpression=Key(self.tierSpecPartitionKey).eq(partitionTargetValue),
				ConsistentRead=False,
				ReturnConsumedCapacity="TOTAL",
			)
			#print dynamodbItem
		except ClientError as e:
			self.logger.warning(e.response['Error']['Message'])
		else:
			# Get the item from the result
			resultItems=dynamodbItem['Items']
			self.logger.debug(resultItems)
			
			for attribute in resultItems:
				# print "Tier Tag Value ==>", attribute['TierTagValue']
				# print "TierStart==>", attribute['TierStart']
				# print '\n'
				# print "TierStop==>", attribute['TierStop']
				# print '\n\n'
				self.tierSpecDict[attribute['TierTagValue']]={'TierStop' : attribute['TierStop'], 'TierStart' : attribute['TierStart']}

			for key, value in self.tierSpecDict.iteritems():
				self.logger.debug('tierSpecDict (key=%s, value=%s)' % (key, value))

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


	def scaleInstance(self, direction):
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
	orch.logger.info("Executing lookupDirectiveSpec()")
	orch.lookupDirectiveSpec('BotoTestCase1')
	orch.logger.info("Executing lookupTierSpec()")
	orch.lookupTierSpec('BotoTestCase1')
	

