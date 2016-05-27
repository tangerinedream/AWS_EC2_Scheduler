# AWS_EC2_Scheduler
Product is meant to allow Stopping and Starting EC2 instances based on a schedule.

Product Features:

Prerequisites:
1. instance role set, allowing SSM agent to operate
2. SSM is installed on each instance
3. Configure a bucket on S3 for SSM processing
4. (Optional) Set Lifecycle Rules on S3 bucket
5. DynamoDB tables


## Ubuntu example
cd /tmp			
curl https://amazon-ssm-us-west-2.s3.amazonaws.com/latest/debian_amd64/amazon-ssm-agent.deb -o amazon-ssm-agent.deb

See also:
  http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/remote-commands-prereq.html
    http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/delegate-commands.html
    http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/install-ssm-agent.html


## DynamoDB Tables
All of the configuration is stored in DynamodDB.  Currently, the provisioning of the tables is not automated but could be done by anyone interested in contributing.  In the meantime, you'll need to provision two tables, and I recommend doing so with a provisioned throughput of 1 unit for both write and read (eventual consistency).  That will set you back about $0.59/month per table.
### WorkloadSpecification
For each workload, there is one entry in the table.  Each workload table entry maps to one or more TierSpecification, based on the number of tiers that comprise the workload.

The workload specification contains tier independent configuration of the workload.  The way to think about it, is the WorkloadSpecification represents the entire system being managed, which consists of one or more tiers.

EnvFilterTagName
:  The name of the tag *key* on the instance which contains a tag *value* indicating the Workload (aka the Environment Name)

EnvFilterTagValue
: The tag *value* containing the Workload identifyier (aka Environment name)

Region
: The AWS region identifier **where the workload runs, not** where the region where this product runs

SpecName
: The name of the Workload

SSMS3BucketName
:  The name of the bucket where the SSM results will be places.  I suggest you enable S3 Lifecycle rules on the bucket as the SSM Agent creates a new entry everytime it checks an instance

SSMS3KeyPrefixName
: The path off the S3BucketName

TierFilterTagName
: The name of the tag *key* on the instance used to locate the Tier's Value.

#### JSON: WorkloadSpecification 
----
{
  "EnvFilterTagName": "Environment",
  "EnvFilterTagValue": "ENV001",
  "Region": "us-west-2",
  "SpecName": "BotoTestCase1",
  "SSMS3BucketName": "myBucketName",
  "SSMS3KeyPrefixName": "ssmRemoteComandResults",
  "TierFilterTagName": "Role"
}
----
### TierSpecification
The tier specification represent the tier-specific configuration.  A tier means as set of instances that share the same Tag Value.  For example, a tier could be "Web", or "App", or "DB", or however your architecture is laid out.  Within a tier, there may be one or more instances.  As there may be multiple rows for a given WorkloadSpecification, each Tier contains the Workload identifier.

The tier specification is somewhat more complex than the WorkloadSpecification, as it contains nested configuration.  That is because a tier has configuration information for Starting, which is different than Stopping.  

Here are a few things you **need** to know about the Tier Specification:
1. The name of *this* tier, is found as the tag value of "TierTagValue" (imagine that).  
   * Each tier name must be unique
2. The Tier Sequence indicates *this* tier's placement is, within the overall sequence.  
   * Numbering starts at **zero**
   * There is no upper bound on sequence number.
   * In the example below, the Tier named "Role_Web" will be the first tier stopped (e.g. TierSequence == 0) and last tier started, in a 3 tier architecture (e.g. TierSequence is 2) 

Definition List

SpecName
:  The name tying the tier back to the WorkloadSpecification

TierStart
:  The dictionary containing a specification for the Start Action of the tier

TierSequence
:  The index within the overall sequence of actioning the WorkloadSpec, for this tier

TierSynchronization
:  Indicator specifying whether the Stop command on the instance is executed asynchronously (defalut), or synchronously. Valid values
  1.  "True", or
  2. *  "False"

TierStop
:  The dictionary containing a specification for the Stop Action of the tier

TierStopOverrideFilename
:  (Optional) The name of the override file in the guest OS to check for existance.  If the file exists in the guest OS,
 the server will not be stopped.

TierStopOverrideOperatingSystem
: (Optional - required if TierStopOverrideFilename set) The name of the OS in the guest.
Valid values
  1.  "Linux", or
  2.  "Windows"

TierTagValue
: The name of the Tag *Value* that will be used as a search target for instances.  The Tag *Key* is specified in the WorkloadSpec.  In other words, the Tag Value for TierTagValue actually names the tier. 

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
    "TierSynchronization": "False"
  },
  "TierTagValue": "Role_Web"
}
----