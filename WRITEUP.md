
## Architecture

### Mapping
There are several modules I wrote to approach the mapping phase for Project Selene. First, the entrypoint `run_mapping.sh` calls `agent.mapping` which dynamically discovers pods through both a BFS approach via `/dependencies` and `/supplies` endpoints and a DNS sweep of known pod IDs on the `selene-net` network for ports 3001-3012.  Discovery lives in `agent.discovery` and fetching endpoints is handled in `agent.client`. The agent then crawls all six endpoints for each pod in an async loop and deterministically computes factors of interest: edge mismatches, in/out degrees, articulation points, single point of failure candidates, capacity utilization, orphans, and dangling references (implemented in `agent.analysis).`  Everything is output to `map.json` with both raw (verbatim endpoint responses) and derived (calculated and cross-refernced) fields. 
### Reporting
The entrypoint for reporting is at `run_reporting.sh` which calls the `agent.reporting` module and makes a single call to `claude-opus-4-7` with the structured map as a prompt and produces the final report in markdown.

An optional module, `agent.llm_tag`, was added for additional LLM input during the agent's mapping phase. This is flag-gated through the `--llm-tag` environment variable. This module is skipped by default, and left here for the reviewers' reference as mentioned in the Design Decisions section.
## Design Decisions

This assessment was implemented mostly using Claude Code. I defined the initial spec, came up with a basic plan, and modified it iteratively through detailed discussions with Claude. Here are a few key design decisions and the approach I decided to go with for each.

### Pod Discovery Method - Declarative vs. Observational Discovery

The spec states all pods should be reachable by discovery through the DNS network. We could either probe with a DNS sweep of ports 3001-3012 or make a BFS gateway traversal from a single pod to all the others by querying their `/dependencies` or `/supplies` endpoints. For this use case, either method would have worked to build the mapping. However, I opted to use a combination of both approaches to utilize the advantages of each. 

By traversing the network via BFS, we can determine relationships based on which other pods they are dependent on as well as single points of failure. This might also reveal orphaned pods, or pods that can't be reached from any other.
From the given list of known pod IDs, we can do a DNS probe to reach these orphaned pods and check for gaps between reachable and orphaned pods. By finding the gap between what infrastructure says exists and what actually exists, we can discover network inconsistencies and configuration drift. 
### Mapping Schema

The mapping schema had to generate derived characteristics and prevent information loss. Creating only derived data could potentially lose important information, while only outputting raw data means the LLM will have to do much more inference and will waste tokens recomputing in-degree or mismatch lists. 
I took the middle ground approach by doing a combination of both: for each pod, include the raw data as well as the computed fields. This also means the mapping only needs to be done once, and the reporting phase will never have to re-crawl the graph. 

### LLM Call Offloading
Another dilemma was deciding whether to make LLM calls during the discovery phase to augment the mapping data with additional LLM analysis, or offload it all to the end in the agent's reporting phase. Claude suggested a hybrid approach where at each pod, the agent could call the model and add additional info that summarizes discrepancies in each pod. Making an additional call to the model would be expensive, and parsing json arrays for missing dependencies would be computationally wasteful if it can just be done programmatically. However, I thought it might be able to help to weed out hidden semantic information that would be missed by the raw data. To test this, I did A/B version testing with the `--llm-tag` when calling the `run_mapping.sh` entrypoint and compared the resulting maps and reports. Ultimately, the difference was negligible. Both reports highlighted the same issues, single points of failure, and caught discrepancies in the logs, since the raw output was still preserved in the mapping.

Therefore, I decided to have only a single LLM call during the reporting phase and none during the mapping phase (leaving `agent.llm_tag` for future reference). The benefit of this is that mapping can be called multiple times for low cost and is idempotent, yielding the same results every time. Although the tradeoff for this is that the single LLM call may eat up the context window and expend many tokens, this call only needs to be made once. And since this network is relatively small with only 12 nodes, this is an ideal use case. Cases where the llm-tag approach could be useful are for larger graphs with more pods, a weaker LLM model where extra summarized tags could help, or for creating a human-readable json map.


## Agent Findings
The agent discovered that although every pod's `/status` endpoint returns `nominal`, there are several discrepancies found from the logs, comms, and the dependency graph. 

**The colony has three single points of failure.** The **Aquifer** pod routes water to 8 other pods, is at 93.3% utilization, and has zero backups. **Helios** provides power to 8 pods as well, is the colony's only articulation point (removing it will separate the graph into different components), and is coupled in a circular dependency with Aquifer. Terminus is the third SPOF which supplies silicon to 3 other pods. A fault in any of these three pods cascades failures through to the others.

**Sixteen dependency mismatches found**. For example, Prometheus lists Aquifer as its dependency, but a log in 2093-09-30 shows that this line was sealed and water was rerouted through Hydroponics for synthesis water. Pharmecuticals now depends on irrigation uptime, which is noted in pod communications but is not reflected in the dependency graph.

**Redundant systems removed**. Several directives such as 2093-089 (Vault water reserve → Sentinel solar expansion budget) and 2094-011 (Vault coolant equipment → Forge repurposing) plus three "infrastructure simplification" entries retired four independent water and coolant backups. This includes Vault's secondary water reserve, Terminus's dual-feed slurry, Zephyr's internal humidity reclaim, and Prometheus's direct Aquifer connection. Each decision saved money at the cost of safety and redundancy.

**Five operationally significant issues are not reflected properly by status endpoint**. Vault flagged an HVAC failure, Hydroponics flagged a CO2 anomaly, Helios is consuming silicon at 140% of forecast, Medica reports pharmacuticals at policy minimum, and Zephyr only surfaces four hours of backup power for atmospheric processors. However, the 2094-07-15 semi-annual safety review still reports that "All pods reporting nominal". The actual status of each pod is not reflected properly by the `/status` endpoint, which is an oversight that may lead to significant degradation of safety and infrastructure in the future. 

## What I'd Do with More Time

With more time, I have several more implementations in mind that would be too complex to finish within the time limit.

One new approach would be to chunk the logs and embed them in a vector store. Then I could have the report agent probe with semantic queries to uncover related items. As a result, cross-pod anomalies and multi-system regressions can become clearer by pulling all relevant chunks through a semantic search which is much clearer than querying each pod individually.

Another solution would be to implement the Plan-and-Execute agentic framework. In this scenario, the graph network is known and every endpoint is listed in our network DNS mapping so we can hardcode the logic in our run_mapping section programmatically. But for large graphs that have unknown schemas, malfunctioning endpoints, or changing topologies, we can approach this in a more dynamic agentic loop. A single head worker can spawn multiple agents armed with tools like `http_get`, `list_endpoints`, and `handle_error` to handle 404s, rate limits, or malformed responses gracefully instead of crashing the pipeline. This would be useful for crawling large networks with unknown schemas. 

On the planning side, I would have spent more time creating a better representation of the dependency graph. The current one generated has several missing edges, and is hard to properly visualize. I would have seen if I could use some external tools to create a better mermaid diagram or just made it look more visually interesting so that humans can evaluate it better. 

I also would have experimented with different prompts for the reporting call to the LLM to determine different types of scenarios and evaluate which one yields the best results.

