#!/usr/bin/env python
import boto3
import logging
import logging.handlers
import time
import watchtower
import requests
from botocore.credentials import InstanceMetadataProvider, InstanceMetadataFetcher
from redo import retriable #https://github.com/mozilla-releng/redo

logger = logging.getLogger('Orchestrator') #The Module Name
auditlogger = logging.getLogger("audit_logger") #getLogger returns a reference to a logger instance with the specified name if it is provided
class InstanceMetaData(object):
     def __init__(self):
            self.ec2 = boto3.client('ec2')

     def getInstanceID(self): #   returns instance tag name from scheduler instance to log to cloudwatch
         response = requests.get('http://169.254.169.254/latest/meta-data/instance-id')
         id = response.text
         self.response = self.ec2.describe_instances(InstanceIds=[id])
         return self.response


     def getInstanceEnvTag(self,response):
        for i in response['Reservations']:  # access reservations list of dict
           for t in i['Instances']:  # access reservations list of dicts
              return ', '.join([x['Value'] for x in t['Tags'] if x['Key'] == 'Environment'])

     def getInstanceNameTag(self,response):
        for i in response['Reservations']:  # access reservations list of dict
           for t in i['Instances']:  # access reservations list of dicts
              return ', '.join([x['Value'] for x in t['Tags'] if x['Key'] == 'Name'])

     def getCredentials(self):
         provider = InstanceMetadataProvider(iam_role_fetcher=InstanceMetadataFetcher(timeout=1000, num_attempts=2))
         creds = provider.load()
         access_key = creds.access_key
         return(access_key)

def initLogging(loglevel,partitionTargetValue,LogStreamName):
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

        filenameVal='Orchestrator_' + partitionTargetValue + '.log' 
        log_formatter = logging.Formatter('[%(asctime)s][P:%(process)d][%(levelname)s][%(module)s:%(funcName)s()][%(lineno)d]%(message)s')

        # Add the rotating file handler
        handler = logging.handlers.RotatingFileHandler(
                filename=filenameVal,
                mode='a',
                maxBytes=128 * 1024,
                backupCount=10)
        handler.setFormatter(log_formatter)

        logger.addHandler(handler)
        logger.setLevel(loggingLevelSelected)
        auditlogger.addHandler(watchtower.CloudWatchLogHandler(log_group='Scheduler',stream_name='Audit'))	#Handler for Audit responsible for dispatch of appropriate Audit info to CW.
        if  (loggingLevelSelected > logging.INFO or loggingLevelSelected == logging.NOTSET ):
            loggingLevelSelected = logging.INFO 
            auditlogger.setLevel(loggingLevelSelected)#  Sets the threshold for this handler to appropriate level. specifies the severity that will be dispatched to the appropriate destination, in this case cloudwatch.
        else:
            auditlogger.setLevel(loggingLevelSelected)
        cloud_handler = watchtower.CloudWatchLogHandler(log_group='Scheduler',stream_name=LogStreamName)
        logger.addHandler(cloud_handler) #This is the Scheduler logs Handler responsible for dispatch of scheduler log messages .InstanceEnvTag is to identify instance log stream and which scheduler that the logs came from. 


class SnsNotifier(object):
	def __init__(self, topic,workload):
		self.topic = topic
		self.workload = workload
	@retriable(attempts=5, sleeptime=0, jitter=0)
	def sendSns(self,subject,message):
		client =boto3.resource('sns')
		topic = client.create_topic(Name=self.topic) # This action is idempotent.
		topic.publish(Subject=subject,Message=str(" Workload : " + self.workload + '\n') + str( " Exception : " + message ))
