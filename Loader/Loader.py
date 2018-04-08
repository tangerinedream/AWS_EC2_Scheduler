import argparse
import os.path
import logging
import yaml
import boto3
from boto3.dynamodb.conditions import Key, Attr

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
  FLEET_SUBSET = 'FleetSubset'

  # ----------------------------------------------------------------------------
  def __init__(self, dynamoDBRegion, logLevel):

    self.initLogging(logLevel) 
    self.dynDb = boto3.resource('dynamodb', region_name=dynamoDBRegion)


  #=======================================================================================================================================
  # Yaml Processing
  #---------------------------------------------------------------------------------------------------------------------------------------
  def isValidYamlFilename(self, fileName):
    logger.info("Yaml File name: " + fileName)
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

    stream = file(yamlFile, 'r')
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
    for currProfileName, currProfileAttrs in tierScalingClause.iteritems():
      if(Loader.FLEET_SUBSET in currProfileAttrs) :
        if(isinstance(currProfileAttrs[Loader.FLEET_SUBSET], basestring)):
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
              if(self.isFleetSubsetStrings(currTier)):
                continue
              else:
                return(False)
          else:
              logger.error('Tier name %s is missing one of the required attributes: %s, %s, %s, or %s ' % (
                 str(currTier),
                 Loader.SPEC_NAME,
                 Loader.TIER_TAG_VALUE,
                 Loader.TIER_START,
                 Loader.TIER_STOP,
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

    # Now, check for non-sequentialness. Interesting fact, since we now know the lists contain the same values, we only need to inspect one of them.
    maxIdx = max(startTierIndexList)
    minIdx = min(startTierIndexList)
          
    # First, did we start at zero?
    if( minIdx != 0 ):
      logger.error('Error: TierSequences must start at Zero')
      return(False)
    
    # Next, did we end at len(startTierIndexList) ?
    if( maxIdx != len(startTierIndexList)-1 ):
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

    logger.info("Deleting workload: %s from Dynamo table: %s" % (self.workloadSpecName, self.workloadTableName) )
    workLoadTable = self.dynDb.Table(self.workloadTableName)

    workLoadTable.delete_item(Key={ Loader.WORKLOAD_PARTITION_KEY : self.workloadSpecName} )

  # ----------------------------------------------------------------------------
  def loadWorkload(self):

    logger.info("Adding workload: %s to Dynamo table: %s" % (self.workloadSpecName, self.workloadTableName) )
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
    logger.info("Deleting Tiers from table: %s" %  (self.tiersTableName))
    for aTier in theTiers:
      primaryKey={Loader.TIER_PARTITION_KEY : aTier[Loader.TIER_PARTITION_KEY], Loader.TIER_SORT_KEY : aTier[Loader.TIER_SORT_KEY] }
      logger.info("Deleting Tier {%s, %s}" % (primaryKey[Loader.TIER_PARTITION_KEY], primaryKey[Loader.TIER_SORT_KEY]))
      tiersTable.delete_item(Key=primaryKey)

  # ----------------------------------------------------------------------------
  def loadTiers(self):

    tiersTable = self.dynDb.Table(self.tiersTableName)
    theTiers = self.tiers
    
    logger.info("Loading Tiers into table: %s" %  (self.tiersTableName))
    for aTier in theTiers:
      logger.info("Loading Tier {%s, %s}" % (aTier.get(Loader.TIER_PARTITION_KEY), aTier.get(Loader.TIER_SORT_KEY)) )
      tiersTable.put_item(Item=aTier)

  # ----------------------------------------------------------------------------
  def loadSpecification(self):
    self.deleteWorkloads()
    self.deleteTiers()

    self.loadWorkload()
    self.loadTiers()

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

    parser.add_argument('-f', '--fileName',       help='StartStop specs file name', required=True)
    parser.add_argument('-r', '--dynamoDBRegion', help='Region where the DynamoDB configuration exists.', required=True)
    parser.add_argument('-v', '--validateOnly', help='Only verify the Yaml file, do not execute any changes', action="store_true", required=False)
    parser.add_argument('-l', '--logLevel', choices=['critical', 'error', 'warning', 'info', 'debug', 'notset'], help='The level to log', required=False)

    args = parser.parse_args()

    if( args.logLevel > 0 ):
      logLevel = args.logLevel
    else:
      logLevel = 'info'

    loader = Loader(args.dynamoDBRegion.strip(), logLevel)

    loader.loadYamlConfig(args.fileName.strip())

    if( loader.isValidSpecification() ):
      if( args.validateOnly ):
        logger.info('--validateOnly flag passed, no changes will execute')
      else:
        loader.loadSpecification()
    else:
        logger.error('Yaml config file did not pass validation, exiting')
        quit(-1)

    logger.info("***Done***")
