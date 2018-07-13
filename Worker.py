#!/usr/bin/python
import boto3
import time
from botocore.exceptions import ClientError
from distutils.util import strtobool
import botocore
import Utils
import logging

__author__ = "Gary Silverman"


logger = logging.getLogger('Orchestrator') #The Module Name

class Worker(object):
    SNS_SUBJECT_PREFIX_WARNING="Warning:"
    SNS_SUBJECT_PREFIX_INFORMATIONAL="Info:"

    def __init__(self, workloadRegion, instance, snsInit,ec2_client,dryRunFlag):

        self.workloadRegion=workloadRegion
        self.instance=instance
        self.dryRunFlag=dryRunFlag
        self.snsInit = snsInit
        self.ec2_client = ec2_client
        self.instanceStateMap = {
            "pending" : 0,
            "running" : 16,
            "shutting-down" : 32,
            "terminated" : 48,
            "stopping" : 64,
            "stopped" : 80
        }

        try:
            self.ec2Resource = boto3.resource('ec2', region_name=self.workloadRegion)
        except Exception as e:
            msg = 'Worker::__init__() Exception obtaining botot3 ec2 resource in region %s -->' % workloadRegion
            logger.error(msg + str(e))
	
class StartWorker(Worker):

    def __init__(self, ddbRegion, workloadRegion, instance, all_elbs, elb, scalingInstanceDelay, dryRunFlag, max_api_request, snsInit,ec2_client):
        super(StartWorker, self).__init__(workloadRegion, instance,snsInit,ec2_client,dryRunFlag)

        self.ddbRegion=ddbRegion
        self.all_elbs=all_elbs
        self.elb=elb
        self.scalingInstanceDelay = scalingInstanceDelay
        self.max_api_request=max_api_request

    def addressELBRegistration(self):
        for i in self.all_elbs['LoadBalancerDescriptions']:
            for j in i['Instances']:
                if j['InstanceId'] == self.instance.id:
                    elb_name = i['LoadBalancerName']
                    logger.info("Instance %s is attached to ELB %s, and will be deregistered and re-registered" % (self.instance.id, elb_name))

                    success_deregister_done=0
                    elb_api_retry_count=1
                    while (success_deregister_done == 0):
                        try:
                            self.elb.deregister_instances_from_load_balancer(LoadBalancerName=elb_name,Instances=[{'InstanceId': self.instance.id}])
                            logger.debug("Succesfully deregistered instance %s from load balancer %s" % (self.instance.id, elb_name))
                            success_deregister_done=1
                        except Exception as e:
                            logger.warning('Worker::addressELBRegistration()::deregister_instances_from_load_balancer() encountered an exception of -->' + str(e))
			    subject_prefix = "Exception - deregister ELB"
			    msg = "Exponential Backoff in progress for deregister_instances_from_load_balancer - EC2 instance %s, sleeping %s seconds" % (self.instance,elb_api_retry_count)
			    self.snsInit.exponentialBackoff(elb_api_retry_count,msg,subject_prefix)
			    elb_api_retry_count+=1

                    success_register_done=0
                    elb_api_retry_count=1
                    while (success_register_done == 0):
                        try:
                            self.elb.register_instances_with_load_balancer(LoadBalancerName=elb_name, Instances=[{'InstanceId': self.instance.id}])
                            logger.debug('Succesfully registered instance %s to load balancer %s' % (self.instance.id, elb_name))
                            success_register_done=1
			except Exception as e:
			    logger.warning('Worker::addressELBRegistration()::register_instances_with_load_balancer() encountered an exception of -->' + str(e))
                            msg = "Exponential Backoff in progress for register_instances_from_load_balancer - EC2 instance %s, sleeping %s seconds" % (self.instance,elb_api_retry_count)
                            subject_prefix = "Exception - register ELB"
                            self.snsInit.exponentialBackoff(elb_api_retry_count,msg,subject_prefix)
                            elb_api_retry_count+=1



    def startInstance(self):

        result='Instance not started'
        if( self.dryRunFlag ):
            logger.warning('DryRun Flag is set - instance will not be started')
        else:
            if self.all_elbs != "0":
                logger.debug('addressELBRegistration() for %s' % self.instance.id)
                self.addressELBRegistration()
                ec2_instance_start = 0
                ec2_start_api_retry_count = 1
                while (ec2_instance_start == 0):
                    logger.debug('Starting EC2 instance: %s' % self.instance.id)
                    try:
                        result=self.instance.start()
                        ec2_instance_start = 1
                        logger.debug('Starting EC2 instance: %s' % self.instance.id)
                        logger.info('startInstance() for ' + self.instance.id + ' result is %s' % result)
                    except Exception as e:
                        msg = 'Worker::instance.start() Exception encountered during instance start ---> %s' % e
                        subject_prefix = "Exception - EC2 instance start"
                        logger.error(msg)
                        self.snsInit.exponentialBackoff(ec2_start_api_retry_count,msg,subject_prefix)
                        ec2_start_api_retry_count += 1

    def scaleInstance(self, modifiedInstanceType):

        instanceState  = self.instance.state
        if (instanceState['Name'] == 'stopped'):


            result='no result'
            if( self.dryRunFlag ):
                logger.warning('DryRun Flag is set - instance will not be scaled')

            else:
                logger.info('Instance [%s] will be scaled to Instance Type [%s]' % (self.instance.id , modifiedInstanceType) )

		# Extract whole instance type from DDB into a list
                modifiedInstanceTypeList = modifiedInstanceType.split('.')		
                targetInstanceFamily = modifiedInstanceTypeList[0]

                # EC2.Instance.modify_attribute()
                # Check and exclude non-optimized instance families. Should be enhanced to be a map.  Currently added as hotfix.
                preventEbsOptimizedList = [ 't2' ]
                if (targetInstanceFamily in preventEbsOptimizedList ):
                    ebsOptimizedAttr = False
		# Check T2 Unlimited	
                    self.t2Unlimited(modifiedInstanceTypeList)
                else:
                    ebsOptimizedAttr = self.instance.ebs_optimized    # May have been set to True or False previously

                instance_type_done=0
                scale_api_retry_count=1
		
		modifiedInstanceTypeValue = modifiedInstanceTypeList[0] + '.' + modifiedInstanceTypeList[1]
                while(instance_type_done == 0):
                    try:
                        result = self.instance.modify_attribute(
                            InstanceType={
                                'Value': modifiedInstanceTypeValue
                            }
                        )
                        instance_type_done=1
                    except Exception as e:
			msg = 'Worker::instance.modify_attribute().modifiedInstanceTypeValue Exponential Backoff in progress for EC2 instance %s, retry count %s, error --> %s ' % (self.instance,scale_api_retry_count,str(e))
			subject_prefix = "Exception ec2.modify_attribute"
			logger.warning(msg)
			self.snsInit.exponentialBackoff(scale_api_retry_count,msg,subject_prefix)
			scale_api_retry_count += 1

                ebs_optimized_done=0
                ebs_optimized_retry_count=1
                while(ebs_optimized_done == 0):
                    try:
                        result = self.instance.modify_attribute(
                            EbsOptimized={
                            'Value': ebsOptimizedAttr
                            }
                        )
                        ebs_optimized_done=1
                    except Exception as e:
			msg = 'Worker::instance.modify_attribute().ebsOptimizedAttr Exponential Backoff in progress for EC2 instance %s, retry count = %s, error --> %s' % (self.instance,ebs_optimized_retry_count,str(e))
		        logger.warning(msg)	
			subject_prefix = "Exception EC2 modify_attribute"
			self.snsInit.exponentialBackoff(ebs_optimized_retry_count,msg,subject_prefix)
			ebs_optimized_retry_count += 1

                # It appears the start instance reads 'modify_attribute' changes as eventually consistent in AWS (assume DynamoDB),
                #    this can cause an issue on instance type change, whereby the LaunchPlan generates an exception.
                #    To mitigate against this, we will introduce a one second sleep delay after modifying an attribute
                time.sleep(self.scalingInstanceDelay)

                logger.info('scaleInstance() for ' + self.instance.id + ' result is %s' % result)
        else:
            logMsg = 'scaleInstance() requested to change instance type for non-stopped instance ' + self.instance.id + ' no action taken'
            logger.warning(logMsg)

    def t2Unlimited(self,modifiedInstanceList):

	if len(modifiedInstanceList) == 3:
		self.modifiedInstanceFlag = modifiedInstanceList[2]
		logger.info('t2Unlimited(): Checking if T2 Unlimited flag is specified in DynamoDB')
		logger.debug('t2Unlimited(): t2_unlimited_flag: %s' % self.modifiedInstanceFlag )
		if ( self.modifiedInstanceFlag == "u") or ( self.modifiedInstanceFlag == "U"):
		    logger.debug('t2Unlimited(): Found T2 Flag in DynamoDB: %s' % self.modifiedInstanceFlag )
		    self.checkT2Unlimited()
		    if self.current_t2_value == "standard":
			t2_unlimited_done=0
			t2_unlimited_retry_count=1
			while(t2_unlimited_done == 0):
			    try:
				logger.info('t2Unlimited(): Trying to modify EC2 instance credit specification')
			        result = self.ec2_client.modify_instance_credit_specification(
				InstanceCreditSpecifications=[
					{
						'InstanceId': self.instance.id,
						'CpuCredits': 'unlimited'
					}
					]
				    )
				logger.info('t2Unlimited(): EC2 instance credit specification modified')
				t2_unlimited_done=1
			    except Exception as e:
				msg = 'Worker::instance.modify_attribute().t2Unlimited Exponential Backoff in progress for EC2 instance %s, retry count = %s, error --> %s' % (self.instance,t2_unlimited_retry_count,str(e))
				logger.warning(msg)
				subject_prefix = "Exception EC2 T2 unlimited"
				self.snsInit.exponentialBackoff(t2_unlimited_retry_count,msg,subject_prefix)
				t2_unlimited_retry_count += 1
	else:
		self.checkT2Unlimited()
		if self.current_t2_value == "unlimited":
	        	logger.debug('t2Unlimited(): Current T2 value: %s' % self.current_t2_value)
			t2_standard_done=0
	      		t2_standard_retry_count=1
			while(t2_standard_done == 0):
			    try:
				logger.info('t2Unlimited(): Trying to modify EC2 instance credit specification')
		        	result = self.ec2_client.modify_instance_credit_specification(
				InstanceCreditSpecifications=[
				    {
					'InstanceId': self.instance.id,
					'CpuCredits': 'standard'
				    }
					]
				    )
				logger.info('t2Unlimited(): EC2 instance credit specification modified')
				t2_standard_done = 1
			    except Exception as e:
				msg = 'Worker::instance.modify_attribute().t2standard Exponential Backoff in progress for EC2 instance %s, retry count = %s, error --> %s' % (self.instance,t2_standard_retry_count,str(e))
				logger.warning(msg)
				subject_prefix = "Exception EC2 T2 unlimited"
				self.snsInit.exponentialBackoff(t2_standard_retry_count,msg,subject_prefix)
				t2_standard_retry_count += 1
    def checkT2Unlimited(self):
	t2_standard_check_done=0
	t2_standard_check_retry_count=1
	while(t2_standard_check_done == 0):
	    try:
		logger.info('check_t2_unlimited(): Checking current T2 value')
		self.current_t2_value = self.ec2_client.describe_instance_credit_specifications(
                InstanceIds=[
                self.instance.id,
                     ]
                )['InstanceCreditSpecifications'][0]['CpuCredits']
		t2_standard_check_done=1
	    except Exception as e:
		msg = 'Worker::describe_instance_credit_specifications() Exponential Backoff in progress for EC2 instance %s, retry count = %s, error --> %s' % (self.instance,t2_standard_check_retry_count,str(e))
		logger.warning(msg)
		subject_prefix = "Exception EC2 T2 unlimited"
		self.snsInit.exponentialBackoff(t2_standard_check_retry_count,msg,subject_prefix)
		t2_standard_check_retry_count += 1
	return self.current_t2_value

    def start(self):
        self.startInstance()

class StopWorker(Worker):
    def __init__(self, ddbRegion, workloadRegion, instance, dryRunFlag,max_api_request,snsInit,ec2_client):
        super(StopWorker, self).__init__(workloadRegion, instance, snsInit, ec2_client,dryRunFlag)

        self.ddbRegion=ddbRegion

        # MUST convert string False to boolean False
        self.waitFlag=strtobool('False')
        self.overrideFlag=strtobool('False')
	self.max_api_request=max_api_request
	self.snsInit=snsInit
    def stopInstance(self):

        logger.debug('Worker::stopInstance() called')

        result='Instance not Stopped'

        if( self.dryRunFlag ):
            logger.warning('DryRun Flag is set - instance will not be stopped')
        else:
            success_instance_stop = 0
            stop_instance_api_retry_count = 1
	    while (success_instance_stop == 0):
	        try:
                    # EC2.Instance.stop()
                        result = self.instance.stop()
                        logger.debug("Succesfully stopped EC2 instance %s" % (self.instance.id))
		        logger.info('stopInstance() for ' + self.instance.id + ' result is %s' % result)
		        success_instance_stop = 1
                except Exception as e:
			msg = 'Worker::instance.stop() Exponential Backoff in progress for EC2 instance %s, retry count = %s, error --> %s' % (self.instance,stop_instance_api_retry_count,str(e))
                        logger.warning(msg)
			subject_prefix = "Exception instance.stop"
			self.snsInit.exponentialBackoff(stop_instance_api_retry_count,msg,subject_prefix)
		        stop_instance_api_retry_count += 1


        # If configured, wait for the stop to complete prior to returning
        logger.debug('The bool value of self.waitFlag %s, is %s' % (self.waitFlag, bool(self.waitFlag)))


        # self.waitFlag has been converted from str to boolean via set method
        if( self.waitFlag ):
            logger.info(self.instance.id + ' :Waiting for Stop to complete...')

            if( self.dryRunFlag ):
                logger.warning('DryRun Flag is set - waiter() will not be employed')
            else:
                try:
                    # Need the Client to get the Waiter
                    ec2Client=self.ec2Resource.meta.client
                    waiter=ec2Client.get_waiter('instance_stopped')

                    # Waits for 40 15 second increments (e.g. up to 10 minutes)
                    waiter.wait( )

                except Exception as e:
                    logger.warning('Worker:: waiter block encountered an exception of -->' + str(e))

        else:
            logger.info(self.instance.id + ' Wait for Stop to complete was not requested')

    def setWaitFlag(self, flag):

        # MUST convert string False to boolean False
        self.waitFlag = strtobool(flag)

    def getWaitFlag(self):
        return( self.waitFlag )
