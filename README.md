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
    1. Create an S3 bucket for the SSM processing
    1. Configure Lifecycle rules on your bucket for 1 day, you shouldn't need more
    1. [Ensure your IAM *instance roles* are enabled for the SSM agent.  Use AmazonEC2RoleforSSM (instance trust policy) and please ensure you understand how SSM works prior to use.](http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/delegate-commands.html, "SSM Instance Role Permissions").  For a full set of SSM Prerequsites, look [Here](http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/remote-commands-prereq.html)
    1. [Install SSM on your target instances](http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/install-ssm-agent.html "Installing SSM")
  1. Create your DynamoDB tables
    1. Ensure correct table naming, and
    1. Ensure correct provisioned throughput
    1. Create a single row for the workload, and one or more rows for each tier.  Details are below.
  1. Address Tag Requirements
    1. Ensure your workload has a unique Tag Key and Tag Value (e.g. Tag Key Name is "Environment", Tag Value is "ENV001"), and that all instances to be orchestrated for that workload are tagged as such.
    1. Ensure each tier within the workload has a unique Tag Key and Tag Value (e.g. Tag Key Name is "Role", Tag Value is "Web", or Tag Value is "DB", etc..)
  1. Enable Cron or Lambda with Scheduling Actions to launch the Orchestrator python script, which does the work.
    1. Ensure your IAM *instance roles* are established to make calls to DynamoDB, EC2, S3, and SSM (Need more detail here)


## DynamoDB Tables
All of the configuration is stored in DynamodDB.  Currently, the provisioning of the tables is not automated but could be done by anyone interested in contributing.  In the meantime, you'll need to provision two tables, and I recommend doing so with a provisioned throughput of 1 unit for both write and read (eventual consistency).  That will set you back about $0.59/month per table.
### WorkloadSpecification
For each workload, there is one entry in the table.  Each workload table entry maps to one or more TierSpecification, based on the number of tiers that comprise the workload.

The workload specification contains tier independent configuration of the workload.  The way to think about it, is the WorkloadSpecification represents the entire system being managed, which consists of one or more tiers.

<dl>
<dt>EnvFilterTagName</dt>
<dd>:  The name of the tag *key* (on the instance) that will be used to group the unique members of this workload.  In the example DynamoDB Table, "Environment" is the tag key. </dd>

<dt>EnvFilterTagValue</dt>
<dd>: The tag *value* identifying the unique set of members within the EnvFilterTagName. In the example DynamoDB Table, "ENV001" is the tag value</dd>

<dt>Region</dt>
<dd>: The AWS region identifier where the _workload_ runs, **not** the region where this open source product is executed</dd>

<dt>SpecName</dt>
<dd>: The unique name of the Workload, the key of the WorkloadSpecification table and foreign key of the TierSpecification table</dd>

<dt>SSMS3BucketName</dt>
<dd>:  The name of the bucket where the SSM results will be places.  *Note*: It is suggested you enable S3 Lifecycle rules on the bucket as the SSM Agent creates a new entry everytime it checks an instance</dd>

<dt>SSMS3KeyPrefixName</dt>
<dd>: The path off the S3BucketName</dd>

<dt>TierFilterTagName</dt>
<dd>: The name of the tag *key* on the instance used to identify the Tier.</dd>

</dl>

#### JSON: WorkloadSpecification 
```json
{
  "EnvFilterTagName": "Environment",
  "EnvFilterTagValue": "ENV001",
  "Region": "us-west-2",
  "SpecName": "BotoTestCase1",
  "SSMS3BucketName": "myBucketName",
  "SSMS3KeyPrefixName": "ssmRemoteComandResults",
  "TierFilterTagName": "Role"
}
```

### TierSpecification
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

<dt>SpecName</dt>
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

<dt>TierTagValue</dt>
<dd>: The name of the Tag *Value* that will be used as a search target for instances for this particular tier.  The Tag *Key* is specified in the WorkloadSpec.</dd>

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
    "TierStopOverrideFilename": "C:\ignore.txt",
    "TierStopOverrideOperatingSystem": "Windows",
    "TierSynchronization": "False"
  },
  "TierTagValue": "Role_Web"
}
```

## The Override Capability