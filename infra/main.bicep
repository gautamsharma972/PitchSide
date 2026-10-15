targetScope = 'resourceGroup'

@description('Region for all resources. Must support the chosen model deployment.')
param location string = resourceGroup().location

@minLength(3)
@maxLength(12)
@description('Short prefix used in resource names.')
param envName string = 'pitchside'

@description('Model deployed in Microsoft Foundry and used by narration + the agent.')
param modelName string = 'gpt-4o'
param modelVersion string = '2024-11-20'

@allowed([ 'GlobalStandard', 'Standard', 'DataZoneStandard' ])
param modelSku string = 'GlobalStandard'

@minValue(1)
@description('Capacity in thousands of tokens per minute.')
param modelCapacity int = 50

@description('Entra user principal names / object IDs to administer the Fabric capacity. Leave empty to skip Fabric capacity.')
param fabricAdmins array = []

@allowed([ 'F2', 'F4', 'F8' ])
param fabricSku string = 'F2'

var suffix = uniqueString(resourceGroup().id)
var foundryName = '${envName}-ai-${suffix}'
var projectName = '${envName}-project'
var tags = { app: 'pitchside' }

// Built-in role definition IDs
var roleAcrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var roleOpenAiUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'     // Cognitive Services OpenAI User
var roleAzureAiUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d'    // Azure AI User (create/run Foundry agents)
var roleEventHubsSender = '2b629674-e913-4c01-ae53-ef4638d8f975'  // Azure Event Hubs Data Sender
var cosmosDataContributor = '00000000-0000-0000-0000-000000000002'

// One user-assigned identity, created first, so role assignments exist before the app pulls/calls anything.
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${envName}-id-${suffix}'
  location: location
  tags: tags
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${envName}-logs-${suffix}'
  location: location
  tags: tags
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'acr${suffix}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

// Microsoft Foundry resource (AIServices) + project + model deployment
resource foundry 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: foundryName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: foundryName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: foundry
  name: projectName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: { displayName: 'PitchSide', description: 'Explainable match intelligence agent' }
}

resource model 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: foundry
  name: modelName
  sku: { name: modelSku, capacity: modelCapacity }
  properties: { model: { format: 'OpenAI', name: modelName, version: modelVersion } }
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: '${envName}-cosmos-${suffix}'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [ { locationName: location, failoverPriority: 0 } ]
    capabilities: [ { name: 'EnableServerless' } ]
    disableLocalAuth: true
    minimalTlsVersion: 'Tls12'
  }
}

resource cosmosDb 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmos
  name: 'pitchside'
  properties: { resource: { id: 'pitchside' } }
}

resource cosmosContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: cosmosDb
  name: 'narrations'
  properties: { resource: { id: 'narrations', partitionKey: { paths: [ '/id' ], kind: 'Hash' } } }
}

// Real-time ingest: app -> Event Hubs -> Fabric Eventstream -> Eventhouse (KQL)
resource ehns 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: '${envName}-eh-${suffix}'
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Standard', capacity: 1 }
  properties: { disableLocalAuth: true, minimumTlsVersion: '1.2' }
}

resource hub 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: ehns
  name: 'match-events'
  properties: { partitionCount: 2, messageRetentionInDays: 1 }
}

resource fabricConsumer 'Microsoft.EventHub/namespaces/eventhubs/consumergroups@2024-01-01' = {
  parent: hub
  name: 'fabric'
}

// Optional Fabric capacity (workspace, lakehouse, eventstream are created in Fabric - see fabric/README.md)
resource fabricCapacity 'Microsoft.Fabric/capacities@2023-11-01' = if (!empty(fabricAdmins)) {
  name: toLower('${envName}fab${take(suffix, 6)}')
  location: location
  tags: tags
  sku: { name: fabricSku, tier: 'Fabric' }
  properties: { administration: { members: fabricAdmins } }
}

resource ehSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: ehns
  name: guid(ehns.id, identity.id, roleEventHubsSender)
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleEventHubsSender)
  }
}

// ---- RBAC for the app identity (no keys anywhere) ----
resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, identity.id, roleAcrPull)
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAcrPull)
  }
}

resource openAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundry
  name: guid(foundry.id, identity.id, roleOpenAiUser)
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleOpenAiUser)
  }
}

resource aiUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundry
  name: guid(foundry.id, identity.id, roleAzureAiUser)
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAzureAiUser)
  }
}

resource cosmosRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmos
  name: guid(cosmos.id, identity.id, cosmosDataContributor)
  properties: {
    principalId: identity.properties.principalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosDataContributor}'
    scope: cosmos.id
  }
}

// ---- Hosting ----
resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${envName}-env-${suffix}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${envName}-web'
  location: location
  tags: union(tags, { 'azd-service-name': 'web' })
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  dependsOn: [ ehSender, acrPull, openAiRole, aiUserRole, cosmosRole, model, cosmosContainer ]
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: { external: true, targetPort: 8000, transport: 'auto' }
      registries: [ { server: acr.properties.loginServer, identity: identity.id } ]
    }
    template: {
      containers: [
        {
          name: 'web'
          image: 'mcr.microsoft.com/k8se/quickstart:latest' // replaced by `azd deploy`
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'AZURE_USE_ENTRA', value: '1' }
            { name: 'AZURE_OPENAI_ENDPOINT', value: 'https://${foundryName}.openai.azure.com/' }
            { name: 'AZURE_OPENAI_DEPLOYMENT', value: modelName }
            { name: 'FOUNDRY_PROJECT_ENDPOINT', value: 'https://${foundryName}.services.ai.azure.com/api/projects/${projectName}' }
            { name: 'FOUNDRY_MODEL', value: modelName }
            { name: 'EVENTHUB_NAMESPACE', value: '${ehns.name}.servicebus.windows.net' }
            { name: 'EVENTHUB_NAME', value: hub.name }
            { name: 'COSMOS_ENDPOINT', value: cosmos.properties.documentEndpoint }
          ]
          probes: [
            { type: 'Readiness', httpGet: { path: '/api/status', port: 8000 } }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 3 }
    }
  }
}

output url string = 'https://${app.properties.configuration.ingress.fqdn}'
output acrLoginServer string = acr.properties.loginServer
output foundryProjectEndpoint string = 'https://${foundryName}.services.ai.azure.com/api/projects/${projectName}'
output eventHubNamespace string = ehns.name
output fabricCapacity string = empty(fabricAdmins) ? '' : fabricCapacity.name
output acrName string = acr.name
output appName string = app.name
