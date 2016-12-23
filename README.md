# AWS_EC2_Scheduler
Welcome to the AWS_EC2_Scheduler, a product meant to orchestrate the Stopping and Starting of non-trivial workloads.

AWS_EC2_Scheduler has the following differentiating features:
  1. Stops and Starts tiers of a workload, rather than individual instances.
  1. Stops and Starts in an orderly fashion, tier by tier.
  1. Orchestrates actions (Start / Stop of tiers) in a specific order you configure.  Stop and Start sequences are independent. 
  1. Works on your existing tags, assuming you have one that indicates the Workload (e.g. Application or Specific Environment) and a tag indicating your tiers, such as "Web" or "DB", etc...
  1. Stops and Starts tiers either asynchronously (default) or synchronously (e.g. waits for each instance to stop before moving to the next instance within the tier).  Tiers can be set independently, for example "Web" and "DB" may be configured for asynchronous, while "DB" tier is set to synchronous.
  1. Any instance may be flagged for bypass.  For example if you had 3 database instances within the "DB" tier, you indicate DB instance #2 should not be Stopped today.  All instances in the "DB" tier would be stopped with the exception of DB #2.
  1. Many workloads can be configured and supported.

## What to do next
Do a once over on the documentation, then try it out.
  1. Address the SSM Prerequisites
    1. Create an S3 bucket for the SSM processing.  ***Note*** Due to a limitation in SSM, the bucket must exist in the same region as where the target ssm instances are running.  Otherwise, SSM will **not** log the result, and as such, this software will poll S3 for 5 minutes per instance waiting for the SSM results to appear in S3, which will never happen. 
    1. Configure Lifecycle rules on your bucket for 1 day, you shouldn't need more
    1. If utilizing the SSM feature, the invoker of this software requires network visibility to the target instances.  If you are running the software outside of EC2, you may need a public IP address on target instances.  You can check for network visibility by using the management console to run an SSM command before attempting to use this software.  If the Management Console (EC2-->Commands-->Command History-->Run Commmand) Filter doesn't show your targeted instances, then you will likely need to add a Public IP, or assign an EIP.  This is an SSM dependency. 
  1. Create your DynamoDB tables
    1. Ensure correct table naming, and
    1. Ensure correct provisioned throughput
    1. Create a single row for the workload, and one or more rows for each tier.  Details are below.
  1. Address Tag Requirements on instances
    1. Ensure your workload has a Tag Key and **unique** Tag Value (e.g. Tag Key Name is "Environment", Tag Value is "ENV001"), and that all instances to be orchestrated for that workload are tagged as such.
    1. Ensure each tier within the workload has a unique Tag Key and Tag Value (e.g. Tag Key Name is "Role", Tag Value is "Web", or Tag Value is "DB", etc..)
  1. Enable Cron or Lambda with Scheduling Actions to launch the Orchestrator python script, which does the work.
    1. If running the product from an instance, ensure both python 2.7.x and boto3 are installed.
    ```
    sudo apt-get install python-pip
    pip install boto3
    ```
    1. If running from Lambda, python and boto3 are preinstalled. 
  1. Ensure IAM Roles and Policies are setup.  You will need an IAM Role for each instance running SSM (if you already have a role, you may simply add the below policy to it).  You will also want an IAM Role for the instance which will actually run the Orchestration script.
    1. For the instances being managed, setup the IAM Role per the following instructions.
      1. [Ensure your IAM *instance roles* are enabled for the SSM agent.  Use AmazonEC2RoleforSSM (instance trust policy) and please ensure you understand how SSM works prior to use.](http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/delegate-commands.html, "SSM Instance Role Permissions").  For a full set of SSM Prerequsites, look [Here](http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/remote-commands-prereq.html)
      1. [Install SSM on your target instances](http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/install-ssm-agent.html "Installing SSM").  Below is an example for Ubuntu, using UserData
      ```
      #!/bin/bash
      cd /tmp			
      curl https://amazon-ssm-us-west-2.s3.amazonaws.com/latest/debian_amd64/amazon-ssm-agent.deb -o amazon-ssm-agent.deb
      dpkg -i amazon-ssm-agent.deb
      systemctl start amazon-ssm-agent
      ```
    1. For the instance from which you will be running the product, you'll need the following Policy enabled for either the instance (if running within AWS), or attached to an IAM user if running outside of AWS.

### IAM Details: instance running the product
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "Stmt1465181592000",
            "Effect": "Allow",
            "Action": [
                "ec2:DescribeInstanceStatus",
                "ec2:DescribeInstances",
                "ec2:DescribeTags",
                "ec2:StartInstances",
                "ec2:StopInstances"
            ],
            "Resource": [
                "*"
            ]
        },
        {
            "Sid": "SNSPublicationClause",
            "Effect": "Allow",
            "Action": [
                "sns:Publish",
                "sns:CreateTopic"
            ],
            "Resource": "arn:aws:sns:us-west-2:<your-account-number-here>:<your-sns-topic(s)>"
        },
        {
            "Sid": "Stmt1465181721000",
            "Effect": "Allow",
			      "Action": [
                "s3:GetObject",
                "s3:ListBucket",
                "s3:GetBucketLocation"
            ],
            "Resource": [
                "arn:aws:s3:::<your-bucket-name-here>",
                "arn:aws:s3:::<your-bucket-name-here>/*"
            ]
        },
        {
            "Sid": "Stmt1465182118000",
            "Effect": "Allow",
            "Action": [
                "dynamodb:GetItem",
                "dynamodb:Query"
            ],
            "Resource": [
                "arn:aws:dynamodb:<region-of-your-dynamodb-tables>:<your-account-number here>:table/WorkloadSpecification",
                "arn:aws:dynamodb:<region-of-your-dynamodb-tables>:<your-account-number here>:table/TierSpecification"
            ]
        },
        {
            "Sid": "Stmt1465183822000",
            "Effect": "Allow",
            "Action": [
                "ssm:SendCommand",
                "ssm:ListDocuments",
                "ssm:DescribeDocument",
                "ssm:GetDocument",
                "ssm:DescribeInstanceInformation",
                "ssm:CancelCommand",
                "ssm:ListCommands",
                "ssm:ListCommandInvocations"
            ],
            "Resource": [
                "*"
            ]
        }
    ]
}
``` 



## DynamoDB Tables
All of the configuration is stored in DynamodDB.  Currently, the provisioning of the tables is not automated but could be done by anyone interested in contributing.  In the meantime, you'll need to provision two tables, and I recommend doing so with a provisioned throughput of 1 unit for both write and read (eventual consistency).  That will set you back about $0.59/month per table.
### WorkloadSpecification
Table Name|Partition Key
----------|-------------
WorkloadSpecification|SpecName

For each workload, there is one entry in the table.  Each workload table entry maps to one or more TierSpecification, based on the number of tiers that comprise the workload.

The workload specification contains tier independent configuration of the workload.  The way to think about it, is the WorkloadSpecification represents the entire system being managed, which consists of one or more tiers.

<dl>
<dt>WorkloadFilterTagName</dt>
<dd>:  The name of the tag *key* (on the instance) that will be used to group the unique members of this workload.  In the example DynamoDB Table, "Environment" is the tag key. </dd>

<dl>
<dt>DisableAllSchedulingActions</dt>
<dd>:  When this attribute is present in the Workload Table and has a string value of '1', <b>no</b> processing will occur across the entire workload.  This attribute is a <b>global override</b> and results in no actions being taken. Any value other than a string of '1', will be ignored and processing will continue as if the attribute was not even present. This attribute is <b>not</b> required</dd>


<dt>WorkloadFilterTagValue</dt>
<dd>: The tag *value* identifying the unique set of members within the EnvFilterTagName. In the example DynamoDB Table, "ENV001" is the tag value</dd>

<dt>WorkloadRegion</dt>
<dd>: The AWS region identifier where the _workload_ runs, **not** the region where this open source product is executed</dd>

<dt>SNSTopicName</dt>
<dd>: The name of the topic to publish SSM related statuses.  For example, whether the override file was set, or other reasons why an instance will not be stopped.</dd>

<dt>SpecName (Primary Key)</dt>
<dd>: The unique name of the Workload, the key of the WorkloadSpecification table and foreign key of the TierSpecification table</dd>

<dt>SSMS3BucketName</dt>
<dd>:  The name of the bucket where the SSM results will be places.  *Note*: It is suggested you enable S3 Lifecycle rules on the bucket as the SSM Agent creates a new entry everytime it checks an instance</dd>

<dt>SSMS3KeyPrefixName</dt>
<dd>: The path off the S3BucketName</dd>

<dt>TierFilterTagName</dt>
<dd>: The name of the tag *key* on the instance used to identify the Tier.</dd>

<dt>VPC_ID</dt>
<dd>: Optional, but recommended, parameter to limit the scope of the query for instance matching. </dd>
</dl>

#### JSON: WorkloadSpecification 
```json
{
  "DisableAllSchedulingActions": "0",
  "WorkloadFilterTagName": "Environment",
  "WorkloadFilterTagValue": "ENV001",
  "WorkloadRegion": "us-west-2",
  "SpecName": "BotoTestCase1",
  "SNSTopicName": "SchedulerTesting",
  "SSMS3BucketName": "myBucketName",
  "SSMS3KeyPrefixName": "ssmRemoteComandResults",
  "TierFilterTagName": "Role",
  "VPC_ID": "vpc-xyz"
}
```

### TierSpecification
Table Name|Partition Key|Sort Key
----------|-------------|--------
TierSpecification|SpecName|TierTagValue

The tier specification represent the tier-specific configuration.  A tier means as set of instances that share the same Tag Value.  For example, a tier could be "Web", or "App", or "DB", or however your architecture is laid out.  Within a tier, there may be one or more instances.  As there may be multiple rows for a given WorkloadSpecification, each Tier contains the Workload identifier.

The tier specification is somewhat more complex than the WorkloadSpecification, as it contains nested configuration.  That is because a tier has configuration information for Starting, which is different than Stopping.  

Here are a few things you **need** to know about the Tier Specification:
  1. The name of *this* tier, is found as the tag value of "TierTagValue" (imagine that).  
    * Each tier name must be unique
  1. The Tier Sequence indicates *this* tier's placement is, within the overall sequence.  
    * Numbering starts at **zero**
    * There is no upper bound on sequence number.
    * In the example below, the Tier named "Role_Web" will be the first tier stopped (e.g. TierSequence == 0) and last tier started, in a 3 tier architecture (e.g. TierSequence is 2) 

Definition List
<dl>

<dt>SpecName (Primary Key)</dt>
<dd>: The unique name of the Workload, the key of the WorkloadSpecification table and foreign key of the TierSpecification table</dd>

<dt>TierStart</dt>
<dd>:  The dictionary containing a specification for the Start Action of the tier</dd>

<dt>TierSequence</dt>
<dd>:  The numeric index within the overall sequence of actioning the WorkloadSpec, for this tier. **NOTE** The index of the "first" tier to be actioned, starts at 0 (e.g. ZERO), not 1 (one).</dd>

<dt>TierSynchronization</dt>
<dd>:  Indicator specifying whether the Stop command on the instance is executed asynchronously (defalut), or synchronously. Valid values are "True" or "False"</dd>

<dt>InterTierOrchestrationDelay</dt>
<dd>:  The number of seconds to delay before actioning on the next tier.  Typically, you have a sense for how long to wait for the current tier to start or stop.  </dd>

<dt>TierStop</dt>
<dd>:  The dictionary containing a specification for the Stop Action of the tier</dd>

<dt>TierStopOverrideFilename</dt>
<dd>:  (Optional) The name of the override file in the guest OS to check for existance.  If the file exists in the guest OS, the server will not be stopped.</dd>

<dt>TierStopOverrideOperatingSystem</dt>
<dd>: (Optional - required if TierStopOverrideFilename set) The name of the OS in the guest.
Valid values are "Linux", or "Windows"</dd>

<dt>TierTagValue (Sort Key)</dt>
<dd>: The name of the Tag *Value* that will be used as a search target for instances for this particular tier.  The Tag *Key* is specified in the WorkloadSpec.</dd>

<dt>TierScaling</dt>
<dd>:  Optional dictionary containing the _Profile_ mapping of user name to instance type and size. Scaling is only used at Start Action time, and is optional. Dictionary is _profile name_ : instance size.  If a tier does not contain an _Profile_ as specified on the command line, no scaling action will occur when the tier is started.</dd>

</dl>

#### JSON: TierSpecification
```json
{
  "SpecName": "BotoTestCase1",
  "TierStart": {
    "TierSequence": "2",
    "TierSynchronization": "False"
  },
  "TierStop": {
    "TierSequence": "0",
    "TierStopOverrideFilename": "/tmp/StopOverride",
    "TierStopOverrideOperatingSystem": "Linux",
    "TierSynchronization": "False",
    "InterTierOrchestrationDelay": "10"
  },
  "TierScaling": {
    "default": "t2.nano",
    "profileB": "t2.medium",
    "profileC": "t2.large",
    "profileD": "c4.large"
  },
  "TierTagValue": "Role_Web"
}
```
Or, for Windows guest OS ...
```json
{
  "SpecName": "BotoTestCase1",
  "TierStart": {
    "TierSequence": "2",
    "TierSynchronization": "False"
  },
  "TierStop": {
    "TierSequence": "0",
    "TierStopOverrideFilename": "C:\\ignore.txt",
    "TierStopOverrideOperatingSystem": "Windows",
    "TierSynchronization": "False"
  },
  "TierScaling": {
    "default": "t2.nano",
    "profileB": "t2.medium",
    "profileC": "t2.large",
    "profileD": "c4.large"
  },
  "TierTagValue": "Role_Web"
}
```

## The Override Capability
Since the product uses SSM, it will always check for the existence of an "override file" on the instance itself.  When present, the product will **not** stop the instance but rather bypass it.  This will be logged in the Orchestrator.log file.

You specify the location of the Override File, as well as the Operating System.  SSM will simply check whether the file exists in the location you configure.  If the file exists, the instance will be bypassed.

SSM will always check for the file, which may introduce some latency in terms of how quickly the instance is attempted for Stop Action.

## Usage
Below are the usage flags for the Orchestrator, which is all you need to launch the product:

### Command Line Options
```

$ python Orchestrator.py -h
usage: Orchestrator.py [-h] -w WORKLOADIDENTIFIER -r DYNAMODBREGION
                       [-a {Stop,Start}] [-t] [-d] [-p SCALINGPROFILE]
                       [-l {critical,error,warning,info,debug,notset}]

Command line parser

optional arguments:
  -h, --help            show this help message and exit
  -w WORKLOADIDENTIFIER, --workloadIdentifier WORKLOADIDENTIFIER
                        Workload Identifier to Action Upon
  -r DYNAMODBREGION, --dynamoDBRegion DYNAMODBREGION
                        Region where the DynamoDB configuration exists. Note:
                        could be different from the target EC2 workload is
                        running
  -a {Stop,Start}, --action {Stop,Start}
                        Action to Orchestrate (e.g. Stop or Start)
  -t, --testcases       Run the test cases
  -d, --dryrun          Run but take no Action
  -p SCALINGPROFILE, --scalingProfile SCALINGPROFILE
                        Resize instances based on Scaling Profile name
  -l {critical,error,warning,info,debug,notset}, --loglevel {critical,error,warning,info,debug,notset}
                        The level to record log messages to the logfile
```

### Stop all of the instances
`$ python Orchestrator.py -w BotoTestCase1 -r us-west-2 -a Stop`

### Start all of the instances
`$ python Orchestrator.py -w BotoTestCase1 -r us-west-2 -a Start`

### Execute a Dry Run, for Stopping all of the instances
`$ python Orchestrator.py -w BotoTestCase1 -r us-west-2 -a Stop -d`

No **Action** will be taken. For example, here, the Stop will not execute.

### Run the Test Suite - Starts and Stops instances with 90 second delay inbetween
`$ python Orchestrator.py -w BotoTestCase1 -r us-west-2 -t`

### Start all of the instances under a particular _profile_, in this case user named as 'performant'
`$ python Orchestrator.py -w BotoTestCase1 -r us-west-2 -p performant -a Start`

