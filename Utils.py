#!/usr/bin/env python
import boto3
import logging
import logging.handlers
import time
import watchtower
import requests
from  botocore.credentials import InstanceMetadataProvider, InstanceMetadataFetcher


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


class RetryNotifier():
	def __init__(self,dynamoDBRegion,sns_workload,max_api_request):
		self.dynamoDBRegion=dynamoDBRegion
		self.snsTopicR = boto3.resource('sns', region_name=self.dynamoDBRegion)
		snsTopic = ''
		snsNotConfigured=False
		self.sns_workload = sns_workload
		self.max_api_request = max_api_request

	def makeTopic(self,sns_topic_name):
			self.sns_topic_name=sns_topic_name
        	        if (self.sns_topic_name):

                	        # Make or retrieve the SNS Topic setup.  Method is Idempotent
                        	try:
                                	self.snsTopic = self.snsTopicR.create_topic( Name=self.sns_topic_name)
	                                self.snsTopicSubjectLine = self.makeTopicSubjectLine(self.sns_topic_name)
        	                except Exception as e:
                	                logger.error('orchestrate() - creating SNS Topic ' + str(e) )
                        	        snsNotConfigured=True
	                else:
        	                snsNotConfigured=True

	def publishTopicMessage(self,subjectPrefix, theMessage):
        	        tagsMsg=''
			publish_message_done=0
	                publish_message_api_retry_count=1
			self.subjectPrefix = subjectPrefix
			while (publish_message_done == 0):

	                	try:	
					self.subject_message = self.snsTopicSubjectLine + ':' + self.subjectPrefix
					if len(self.subject_message) > 99:
						logger.warning("SNS Subject too long, truncating to 99 characters - original message ->" + self.subject_message)
						self.subject_message = self.subject_message[:99]
		                    	self.snsTopic.publish(
      		                	    Subject=self.subject_message,
	        		            Message=theMessage + "\n" + tagsMsg
       	        			)
					publish_message_done=1
	                	except Exception as e:
        	                	logger.error('publishSNSTopicMessage() ' + str(e) )
					if (publish_message_api_retry_count > self.max_api_request ):
						msg = 'Maximum API Call Retries for snsTopic.publish() reached, exiting program'
						logger.error(msg + str(publish_message_api_retry_count))
					else:
						publish_message_api_retry_count+=1
	                                        logger.warning('Exponential Backoff in progress for snsTopic.publish(), retry count = %s' % str(publish_message_api_retry_count))
						sleepTime = pow(float(2), float(publish_message_api_retry_count))
						time.sleep(sleepTime)


	def makeTopicSubjectLine(self,subjectText):
                res = 'AWS_EC2_Scheduler Notification:  Workload==>' + subjectText
                return( res )

	def exponentialBackoff(self,count,msg,subject_prefix):
                try:
                        sleepTime = pow(float(2), float(count))
                        logger.info(msg + str(sleepTime))
			self.count = count
			self.msg = msg
			self.subject_prefix = subject_prefix

                        # This is to ensure that we are sending SNS notifications only after 3rd count
                        if (self.count == 3):
                                try:
                                        self.publishTopicMessage(self.subject_prefix, self.msg)
					logger.warning('exponentialBackoff(): Sending SNS notification for Retry')
                                        logger.info(msg)
                                except Exception as e:
                                        msg = 'sending SNS message failed with error -->'
                                        logger.error(msg + str(e))
			elif (self.count > self.max_api_request):
				try:
					max_subject_prefix = "Max Retries RateLimitExceeded"
					max_msg = 'Max Retries RateLimitExceeded' + msg
                                        self.publishTopicMessage(max_subject_prefix, max_msg)
                                        logger.info('Sending SNS notification for Max Retries RateLimitExceeded - DescribeInstances')
                                except Exception as e:
                                        logger.warning('Orchestrator::publishSNSTopicMessage() encountered an exception of -->' + str(e))
                                exit()
		
                        time.sleep(sleepTime)

                except Exception as e:
                        msg = 'exponentialBackoff failed with error %s -->'
                        logger.error(msg + str(e))



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
        auditlogger.addHandler(watchtower.CloudWatchLogHandler(log_group='Scheduler',stream_name='TESTREDO'))	#Handler for Audit responsible for dispatch of appropriate Audit info to CW.
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
	def sendSns(self,subject,message):
		client =boto3.resource('sns')
		topic = client.create_topic(Name=self.topic)
		topic.publish(Subject=subject,Message=str( self.workload ) + str( message ))