# AWS_EC2_Scheduler
Product is meant to allow Stopping and Starting EC2 instances based on a schedule.

Product Features:

Prerequisites:
1. instance role set, allowing SSM agent to operate
2. SSM is installed on each instance
3. Configure a bucket on S3 for SSM processing
4. (Optional) Set Lifecycle Rules on S3 bucket

# Ubuntu example
cd /tmp			
curl https://amazon-ssm-us-west-2.s3.amazonaws.com/latest/debian_amd64/amazon-ssm-agent.deb -o amazon-ssm-agent.deb

See also:
  http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/remote-commands-prereq.html
    http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/delegate-commands.html
    http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/install-ssm-agent.html


