# Architecture

Citation Lens gives a coding agent (Codex or Claude Code) five research tools.
The agent decides what to search, which papers to trust and what to write.
Lens does the slow, parallel and bookkeeping-heavy work: querying scholarly indexes, walking the citation graph, ranking, caching and keeping each response small.

## The whole system

```mermaid
flowchart LR
    U(["You: a research question"]) --> A

    subgraph HOST["Host agent: Codex or Claude Code"]
        A["Agent<br/>screens, verifies, writes"]
        P["Playbook<br/>skills/research/SKILL.md"]
        A --- P
    end

    A <-->|"MCP over stdio<br/>compact JSON cards"| T

    subgraph LENS["Citation Lens MCP server"]
        T["research_search<br/>research_expand<br/>research_graph<br/>research_read<br/>research_visual"]
        R["Ranking<br/>fuse, merge, lanes"]
        N["Pooled HTTP client<br/>pacing, retries, deadlines,<br/>throttle cooldown"]
        C[("Local SQLite<br/>HTTP cache and<br/>graph snapshots")]
        T --> R --> N
        N <--> C
        T <--> C
    end

    N --> S2["Semantic Scholar<br/>search, references,<br/>citing papers, similar papers"]
    N --> OA["OpenAlex<br/>search, most-cited<br/>citing papers"]
    N --> AX["arXiv<br/>newest preprints,<br/>abstracts, HTML text,<br/>bibliographies"]
    N --> CR["Crossref<br/>exact DOI metadata"]

    A --> O(["Answer: papers with quotes,<br/>citing to cited links, gaps"])
```

What stays in Lens: query fan-out, de-duplication, ranking, paging, caching.
What stays with the agent: deciding what counts as relevant and what to claim.

## One research session

A typical run is four Lens calls.
Search and expansion return cards, not papers; reading is on demand.

```mermaid
sequenceDiagram
    autonumber
    actor You
    participant Agent as Agent (Codex / Claude Code)
    participant Lens as Citation Lens
    participant Web as Scholarly indexes

    You->>Agent: Research question
    Agent->>Agent: Scope: approaches, exclusions, budget
    Agent->>Lens: research_search(2-4 query variants)
    par every query on every provider
        Lens->>Web: Semantic Scholar
        Lens->>Web: OpenAlex
        Lens->>Web: arXiv, relevance and newest
    end
    Web-->>Lens: raw results
    Lens-->>Agent: about 20 fused cards and a graph_id

    Agent->>Agent: Pick 3-6 seed papers
    Agent->>Lens: research_expand(seed_ids, query)
    par for every seed
        Lens->>Web: references
        Lens->>Web: citing papers
        Lens->>Web: similar papers
    end
    Lens->>Web: reference lists of top candidates, one batch
    Lens-->>Agent: up to 60 cards in 3 lanes plus citing to cited edges

    Agent->>Agent: Screen cards by title, why and snippet
    Agent->>Lens: research_read(ids, part=abstract)
    Lens-->>Agent: up to 30 abstracts, only for unsettled papers
    Agent-->>You: Papers with verbatim quotes, citation links, gaps
```

## What research_expand does

This is the Connected-Papers-style part.
One call turns a few seed papers into a ranked, labelled neighbourhood.

```mermaid
flowchart TD
    S["Seed papers, 1-8"] --> G

    subgraph G["Gather in parallel"]
        direction LR
        G1["References<br/>what the seed cites"]
        G2["Citing papers<br/>newest and most cited"]
        G3["Similar papers<br/>first 4 seeds"]
    end

    G --> M["Merge duplicates<br/>same arXiv ID, DOI or exact title<br/>distinct arXiv IDs never merge"]
    M --> H["Fetch reference lists of the top 400 candidates<br/>edges between candidates are added"]
    H --> F["Score each candidate"]

    F --> F1["Bibliographic coupling<br/>shared references with the seeds,<br/>rare references weigh more"]
    F --> F2["Co-citation and in-degree<br/>weighted by how on-topic<br/>each citing paper is"]
    F --> F3["Query match, citations,<br/>citations per year, similarity"]

    F1 --> K
    F2 --> K
    F3 --> K
    K{"Keep?<br/>matches the query, resembles a seed,<br/>or cited by relevant papers"}
    K -- no --> X["Dropped: tool-users and<br/>off-topic bibliography entries"]
    K -- yes --> L

    subgraph L["Three lanes, interleaved"]
        direction LR
        L1["Foundation<br/>older, cited by the graph"]
        L2["Follow-up<br/>older, cites or resembles seeds"]
        L3["Recent<br/>last two years"]
    end

    L --> OUT["Cards: title, year, citations, URL,<br/>lane, why, verbatim snippet<br/>plus citing to cited edges"]
```

Each card says why it is there, for example `cites 3/3 seeds; shares 10 seed refs; cited by 3 graph papers`.
A card is about 500 bytes; pages hold at most 60 cards and 120 edges, and later pages come from the local snapshot without new requests.

## When providers fail

Free providers throttle, and Lens treats that as normal rather than fatal.

```mermaid
flowchart LR
    Q["Request"] --> B{"Host paused?"}
    B -- yes --> E1["Fail fast:<br/>reported in errors"]
    B -- no --> D{"Cached?"}
    D -- yes --> OK["Use it"]
    D -- no --> H["Send with host pacing"]
    H --> R{"Answer"}
    R -- "200" --> OK
    R -- "429 or 5xx" --> RT{"Retries left<br/>and wait short?"}
    RT -- yes --> H
    RT -- no --> P["Pause that host for 60 s"] --> E1
    E1 --> FB["Fallback provider or<br/>arXiv bibliography"]
```

- Every provider call has its own deadline, so one slow index cannot hold a whole tool call.
- Failures are listed in the response (`errors`, `searches`), never silently dropped.
- Backward references can fall back from Semantic Scholar to OpenAlex, then to the paper's own arXiv bibliography; forward citations have no such primary source.

## Why edges can be trusted

```mermaid
flowchart LR
    A["Paper A"] -->|"cites: a reference list says so"| B["Paper B"]
    A -. "similar to or shares references with" .-> C["Paper C"]
```

Solid arrows are returned as edges, always `[citing, cited]`.
Similarity and shared references are labelled in each card's `why` and are never reported as citations.
