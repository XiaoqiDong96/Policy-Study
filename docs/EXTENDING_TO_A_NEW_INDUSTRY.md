# Extending to a New Industry

This guide describes the minimum work needed to reuse the pipeline for another industry.

## 1. Define the research boundary

Write a short domain specification before adding keywords:

- included products, services, technologies, firms, infrastructure, and value-chain links
- adjacent concepts that count only with industrial context
- public-service, administrative, social, or cultural uses of the same words that must be excluded
- official taxonomy or policy source used to divide the industry into subcategories
- required administrative panel level

The domain definition should be stable enough for two researchers to code a sample independently.

## 2. Build deterministic candidate screening

Create a screening module modeled on the existing domain screeners. Use:

- strong phrases that are sufficient by themselves
- weak phrases that require a nearby industrial or policy-context term
- title and body matches recorded separately
- exclusion phrases for recurring false positives
- explicit evidence fields showing why a document entered the candidate set

HTML should be parsed before matching. Normalize whitespace and punctuation, preserve the original title and body, and keep screening deterministic.

Audit a stratified sample containing positives, near misses, short notices, broad planning documents, and obvious false positives. Revise the rules before sending the full candidate package to an LLM.

## 3. Add a domain specification

Register a `DomainSpec` in `scripts/domain_policy_pipeline.py`. It should contain:

- `domain_key` and a readable label
- the related-policy output field
- a concise inclusion scope
- a concise exclusion scope
- segment hints or industry categories

Keep the shared definition of industrial policy unchanged across industries. Domain relevance and industrial-policy status are separate judgments.

## 4. Run industrial-policy classification

The first stage asks whether the government document intentionally shapes the development, structure, capacity, technology, location, entry, exit, or market conditions of the target industry. Record a probability-like confidence value and short evidence.

Use a fast model for the full candidate set. Send only boundary-confidence cases, failures, and defined edge cases to a second model. Preserve both votes and the final decision.

## 5. Classify policy instruments

For documents classified as industrial policy, code the established instrument dimensions, including:

- ex ante or ex post intervention
- supply-side or demand-side intervention
- support or restriction
- concrete measures versus directional guidance only
- instrument categories, intensity, target, coverage, and supporting excerpts

Allow multiple instruments in one document. Aggregate document-level indicators only after preserving the original multi-label result.

## 6. Build panels

Construct separate monthly panels for central, provincial, and prefecture policies unless the research design specifies another unit. Do not automatically copy a central policy into every lower-level unit.

For a category-specific study, retain both the overall industry panel and a long panel indexed by month, administrative unit, and industry category. Document how multi-category policies contribute to counts, intensity, and coverage.

## 7. Validate before a full run

- compile all Python files
- run a small deterministic screening sample
- manually review candidate precision and recall
- run a small LLM batch and inspect parse failures
- confirm resume behavior by interrupting and restarting
- verify that panel totals reconcile with document-level results
- save boundary and disagreement cases for human audit
