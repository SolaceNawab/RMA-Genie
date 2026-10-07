# coop_support_hack
This is the repo for hackathon

## Plugins

This repo is a Claude Code plugin marketplace.

```
/plugin marketplace add SolaceNawab/coop_support_hack
/plugin install support-rma@coop-support-hack
/plugin install support-jira@coop-support-hack
/plugin install support-rma-jira@coop-support-hack
```

| Plugin | What it does |
|---|---|
| [support-rma](plugins/support-SupportPlans) | `/support-rma:inventory`: available serial numbers from the live Shipment Inventory Record (RMA generation to follow) |
| [support-jira](plugins/support-JiraCreation) | `/support-jira:draft-jira`: collect serial number and support plan, then decrypt and extract the gather-diagnostics bundle (Jira drafting to follow) |
| [support-rma-jira](plugins/support-JiraTemplate) | `/support-rma-jira:raise-rma`: build the OPS RMA Jira from an extracted gather-diagnostics bundle (Summary/Description per the Hardware Replacement Workflow), preview for edits, then create it via the Atlassian MCP |
