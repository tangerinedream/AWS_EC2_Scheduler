#!/usr/bin/env python3
import argparse
import os.path
import logging
import yaml
import boto3
from boto3.dynamodb.conditions import Key, Attr
import datetime
import re

logger = logging.getLogger('Loader') #The Module Name

class Loader(object):
  WORKLOAD_PARTITION_KEY = 'SpecName'
  WORKLOAD_TAG_NAME='WorkloadFilterTagName' 
  WORKLOAD_TAG_VALUE='WorkloadFilterTagValue'
  WORKLOAD_TIER_TAG_NAME='TierFilterTagName'
  TIER_PARTITION_KEY = 'SpecName'
  TIER_SORT_KEY = 'TierTagValue'
  TIER_SCALING = 'TierScaling'
  SPEC_NAME= 'SpecName'
  TIER_TAG_VALUE = 'TierTagValue'
  TIER_START = 'TierStart'
  TIER_STOP = 'TierStop'
  #TIER_ORCHESTRATION_DELAY = 'InterTierOrchestrationDelay'
  FLEET_SUBSET = 'FleetSubset'
  WORKLOADSTATE = 'WorkloadState'
  WORKLOADSPECTYPE = 'Unmanaged'

  # ----------------------------------------------------------------------------
  def __init__(self, dynamoDBRegion, logLevel):

    self.initLogging(logLevel) 
    self.dynDb = boto3.resource('dynamodb', region_name=dynamoDBRegion)
    self.currentTime = str(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    self.workloadSpecType = Loader.WORKLOADSPECTYPE
  #=======================================================================================================================================
  # DDB Unload, YAML file deletion and copy workload
  #---------------------------------------------------------------------------------------------------------------------------------------
  def copy_workload(self):
      # determine older environment name from source_specname filename passed
      # determine new environment name to replace old environment name with from file passed.

      sourceWorkloadName = os.path.split(args.sourceWorkloadPath)
      newWorkloadName = os.path.split(args.newWorkloadPath)

      logger.info("sourceWorkloadName {}\n".format(sourceWorkloadName[1]))
      logger.info("newWorkloadName {}\n".format(newWorkloadName[1]))

      source = re.search("(?<=Quest-)([A-Z]{2}\d{2})", sourceWorkloadName[1])
      orig_env = source.group(0)
      dest = re.search("(?<=Quest-)([A-Z]{2}\d{2})", newWorkloadName[1])
      new_env = dest.group(0)


      # open file for reading
      try:
          with open(args.sourceWorkloadPath, 'r+') as readfile:
              filedata = readfile.read()  # loads file into memory and find older environment name and replace with new name
              updatedfile = filedata.replace(orig_env, new_env)  # write out updated file to new_filename

      except Exception as e:
          print(e)
      # open file for writing
      try:
          with open(args.newWorkloadPath, 'w') as writefile:
              writefile.write(updatedfile)
              print ("replaced {} with {} in {} ".format(orig_env, new_env, args.newWorkloadPath))
      except Exception as e:
          print(e)

  def unload_workload(self):  #unloads the workload form 3 tables and removes from filesystem.
      unLoadFile = args.unloadFilePath.strip()
      self.deleteWorkloads()
      self.deleteTiers()
      self.deleteWorkLoadState()
      print ("Unloaded Workload {}".format(unLoadFile))
      logger.info("Unloaded Workload {}".format(unLoadFile))
      if os.path.isfile(unLoadFile):
          os.remove(unLoadFile)
          print ("Deleted File from {} ".format(unLoadFile))
      else:
          print(unLoadFile, 'File does not exist')
  #=======================================================================================================================================
  # Yaml Processing
  #---------------------------------------------------------------------------------------------------------------------------------------
  def isValidYamlFilename(self, fileName):
    logger.info("Yaml File name: {}\n".format(fileName) )
    if os.path.exists(fileName) == False:
      logger.error("Yaml File %s doesn't exist, exiting." % fileName)
      return(False)
  
    fileExt = fileName.rpartition('.')[len(fileName.rpartition('.'))-1]
  
    if fileExt == 'yaml':
      return(True)
    else:
      logger.error("File type %s not supported" % fileExt )
      return(False)

  def loadYamlConfig(self, yamlFile):
    if( self.isValidYamlFilename(yamlFile) == False ):
      logger.error("File must exist and be named with .yaml  Exiting")
      quit(-1)

    stream = open(yamlFile, 'r')
    yaml_doc = yaml.load(stream)


    workloads = yaml_doc.get("workloads")
    self.workloadTableName = workloads.get("table")
    self.workloadBlock = workloads.get("workload")
    self.workloadSpecName = self.workloadBlock.get("SpecName")

    topLevelTiersBlock = yaml_doc.get("tiers")
    self.tiersTableName = topLevelTiersBlock.get("table")
    self.tiers= topLevelTiersBlock.get("tiers") # this 'tiers' is a child in the tree of the top level 'tiers'

  def isFleetSubsetStrings(self, tierBlock):
    tierScalingClause =  tierBlock[Loader.TIER_SCALING]
    for currProfileName, currProfileAttrs in tierScalingClause.items():
      if(Loader.FLEET_SUBSET in currProfileAttrs) :
        if(isinstance(currProfileAttrs[Loader.FLEET_SUBSET], str)):
          continue
        else:
          logger.error(
              'In Tier %s, the Profile name %s contains a FleetSubset entry which is not a String ->%s<-.  Please quote the value(s) in the yaml file for this attribute' % (
              str(tierBlock[Loader.TIER_TAG_VALUE]),
              str(currProfileName),
              currProfileAttrs[Loader.FLEET_SUBSET]
            )
          )
          return(False)
      else:
        continue
        
    return(True)



  def isRequiredAttributes(self):

    if( Loader.WORKLOAD_TAG_NAME and Loader.WORKLOAD_TAG_VALUE and Loader.WORKLOAD_TIER_TAG_NAME in self.workloadBlock):

      for currTier in self.tiers:
          if Loader.SPEC_NAME and Loader.TIER_TAG_VALUE and Loader.TIER_START and Loader.TIER_STOP in currTier: 
              if (Loader.TIER_SCALING in currTier):
                  logger.info('TierScaling found for Tier name %s' % currTier)
                  self.isFleetSubsetStrings(currTier)
          else:
              logger.error('Tier name %s is missing one of the required attributes: %s, %s, %s or %s ' % (
                 str(currTier),
                 Loader.SPEC_NAME,
                 Loader.TIER_TAG_VALUE,
                 Loader.TIER_START,
                 Loader.TIER_STOP
              ))
              return(False)

    else:
      logger.error('Workload %s is missing one of the required attributes: %s, %s, or %s ' % (
        str(currTier),
        Loader.WORKLOAD_TAG_NAME,
        Loader.WORKLOAD_TAG_VALUE,
        Loader.WORKLOAD_TIER_TAG_NAME
      ))
      return(False)

    return(True)

  def isRequiredSequencing(self):
      
    # Construct the list
    startTierIndexList = []
    stopTierIndexList = []
    for aTier in self.tiers:
       startTierIndexList.append( aTier["TierStart"]["TierSequence"] )
       stopTierIndexList.append( aTier["TierStop"]["TierSequence"]  )
    
    # Sort the list for the diff
    startTierIndexList= sorted( startTierIndexList )
    stopTierIndexList = sorted( stopTierIndexList )
    
    logger.debug('startTierIndexList is %s' % startTierIndexList)
    logger.debug('stopTierIndexList  is %s' %  stopTierIndexList)
    
    # If there are any differences, there's a problem
    symmetricDifferenceOfTierIndexLists=set(startTierIndexList) ^ set(stopTierIndexList)
    isDifferent = bool(symmetricDifferenceOfTierIndexLists)
    if( isDifferent ):
      logger.error('Error: Start and Stop tiers contain different indexes. Here are the differences: mismatched TierSequences %s, Start TierSequences %s, Stop TierSequences %s' % (symmetricDifferenceOfTierIndexLists, startTierIndexList, stopTierIndexList ))
      return (False)

    # Now, check for non-sequentialness. 
    #   Interesting fact, since we now know the lists contain the same values, we only need to inspect one of them.
    #   Another interesting fact, using set to ensure uniqueness
    startSet = set(startTierIndexList)
    maxIdx = max(startSet)
    minIdx = min(startSet)
          
    # First, did we start at zero?
    if( minIdx != 0 ):
      logger.error('Error: TierSequences must start at Zero')
      return(False)
    
    # Next, did we end at len(startTierIndexList) ?
    if( maxIdx != len(startSet)-1 ):
      logger.error('Error: Tier Start and Tier Stop TierSequences must be sequential starting at zero and without gaps, nor duplicates. Index List is %s' % startTierIndexList)
      return(False)

    return(True)
    
  def isValidSpecification(self):

    if( self.isRequiredAttributes() ):
      if( self.isRequiredSequencing() ):
        return(True)

    return(False)


  #=======================================================================================================================================
  # DynamoDB Processing
  #---------------------------------------------------------------------------------------------------------------------------------------
  def deleteWorkloads(self):

    logger.info("Deleting workload: %s from Dynamo table: %s\n" % (self.workloadSpecName, self.workloadTableName) )
    workLoadTable = self.dynDb.Table(self.workloadTableName)

    workLoadTable.delete_item(Key={ Loader.WORKLOAD_PARTITION_KEY : self.workloadSpecName} )

  # ----------------------------------------------------------------------------
  def loadWorkload(self):

    logger.info("Adding workload: %s to Dynamo table: %s\n" % (self.workloadSpecName, self.workloadTableName) )
    workLoadTable = self.dynDb.Table(self.workloadTableName)

    workLoadTable.put_item(Item=self.workloadBlock)

  # ----------------------------------------------------------------------------
  def deleteTiers(self):

    tiersTable = self.dynDb.Table(self.tiersTableName)

    firstTier = self.tiers[0]

    # Get all items matching the Partition Key whose value is SpecName on the first element in the YAML file
    response = tiersTable.query( KeyConditionExpression=Key(Loader.TIER_PARTITION_KEY).eq(firstTier[Loader.TIER_PARTITION_KEY]) )
    theTiers = response['Items']

    # Tier Table dynamo calls require both keys, Partition and Sort
    logger.info("Deleting Tiers from table: {}\n".format(self.tiersTableName))
    for aTier in theTiers:
      primaryKey={Loader.TIER_PARTITION_KEY : aTier[Loader.TIER_PARTITION_KEY], Loader.TIER_SORT_KEY : aTier[Loader.TIER_SORT_KEY] }
      logger.info("Deleting Tier {%s, %s}" % (primaryKey[Loader.TIER_PARTITION_KEY], primaryKey[Loader.TIER_SORT_KEY]))
      tiersTable.delete_item(Key=primaryKey)

  # ----------------------------------------------------------------------------
  def loadTiers(self):

    tiersTable = self.dynDb.Table(self.tiersTableName)
    theTiers = self.tiers
    
    logger.info("Loading Tiers into table: {} \n".format(self.tiersTableName))
    for aTier in theTiers:
      logger.info("Loading Tier {%s, %s}" % (aTier.get(Loader.TIER_PARTITION_KEY), aTier.get(Loader.TIER_SORT_KEY)) )
      tiersTable.put_item(Item=aTier)

  # ----------------------------------------------------------------------------
  def loadSpecification(self):
    self.deleteWorkloads()
    self.deleteTiers()
    self.deleteWorkLoadState()

    self.loadWorkload()
    self.loadTiers()
    self.workLoadState()


  def deleteWorkLoadState(self):

    try:
        print("Deleting itemKey Workload value {} from {} table \n".format(self.workloadSpecName,self.WORKLOADSTATE))
        self.WorkloadStateTable = self.dynDb.Table(self.WORKLOADSTATE)
        self.WorkloadStateTable.delete_item(Key={"Workload":self.workloadSpecName})

    except Exception as e:
        print(e)
        logger.info("Exception deleteWorkLoadState {}".format(e))


  def workLoadState(self):

    try:
        self.WorkloadStateTable = self.dynDb.Table(self.WORKLOADSTATE)
        print("Loading {} to table {} \n".format(self.workloadSpecName,self.WORKLOADSTATE))
        self.WorkloadStateTable.put_item(
              Item={
              'Workload': self.workloadSpecName,
              'LastActionTime': str(self.currentTime),
              'LastActionType': self.workloadSpecType
             },
             ConditionExpression = "attribute_not_exists(Workload)")   
        logger.info("Updated WorkloadState DynamoDB")
        print("Updated WorkloadState DynamoDB")
    except Exception as e:
        print(e)
        msg = 'Exception encountered during DDB put_item %s -->' % e
        logger.error(msg + str(e))
        raise e

  def initLogging(self, loglevel):
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

     sh = logging.StreamHandler()
     logFormatter = logging.Formatter('[%(asctime)s][P:%(process)d][%(levelname)s][%(module)s:%(funcName)s()][%(lineno)d]%(message)s')
     sh.setFormatter(logFormatter)
     logger.addHandler(sh)
     logger.setLevel(loggingLevelSelected)

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Command line parser')

    parser.add_argument('-f', '--fileName',       help='YAML Specification file name', required=False)
    parser.add_argument('-r', '--dynamoDBRegion', help='Region where the DynamoDB configuration exists.', required=False)
    parser.add_argument('-v', '--validateOnly',   help='Only verify the Yaml file, do not execute any changes', action="store_true", required=False)
    parser.add_argument('-l', '--logLevel',       choices=['critical', 'error', 'warning', 'info', 'debug', 'notset'], help='The level to log', required=False)

    parser.add_argument('-s', '--sourceWorkloadPath',help="Provide the relative path of the source workload yaml file e.g  ../../AWS-Scheduling/loadDynamoDB/workloads/Quest-QA09.yaml",required=False)
    parser.add_argument('-d', '--newWorkloadPath',   help="Provide the relative path of the destination workload yaml file e.g  ../../AWS-Scheduling/loadDynamoDB/workloads/Quest-QA10.yaml",required=False)
    parser.add_argument('-u', '--unloadFilePath',help="Provide the relative path of the workload yaml file to delete from filesystem and unload from DDB e.g  ../../AWS-Scheduling/loadDynamoDB/workloads/Quest-QA10.yaml",required=False)

    args = parser.parse_args()
    logger.info("args {}\n".format(args))
    print("args passed are {} \n ".format(args))

    if( args.logLevel is not None):
      logLevel = args.logLevel
    else:
      logLevel = 'info'

    loader = Loader(args.dynamoDBRegion.strip(), logLevel)


    if args.unloadFilePath is not None:
        loader.loadYamlConfig(args.unloadFilePath.strip())
        loader.unload_workload()

    elif args.sourceWorkloadPath is not None:
        loader.copy_workload()

    elif args.fileName is not None:
        loader.loadYamlConfig(args.fileName.strip())


        if( loader.isValidSpecification() ):
            if( args.validateOnly ):
                logger.info('--validateOnly flag passed, no changes will execute')
            else:
                logger.info("Run self.deleteWorkloads ,self.deleteTiers followed by self.loadWorkload,self.loadTiers\n")
                loader.loadSpecification()

        else:
            logger.error('Yaml config file did not pass validation, exiting')
            quit(-1)
    else:
        logger.info("***Done***")
