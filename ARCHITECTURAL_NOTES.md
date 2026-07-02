# Architectural Notes

## SchemaMapper Usage & Necessity

**Is SchemaMapper still used?**

YES — it's actively used by multiple services and utilities:

1. **ArtifactValidator** (`src/utils/artifact_validator.py`):
   - Calls `schemas.load_raw()` to get all artifact schemas
   - Uses metadata from schema dicts to match artifacts and validate against Draft-07 schemas
   - Validates section structure using metadata from x-artifact.sections

2. **SchemaChecker** (`src/services/schema_checker.py`):
   - `catalog()` method: verifies schema registry integrity + template claims
   - `check_json()` method: validates native JSON artifacts directly against schemas

3. **CelEvaluator** (`src/services/cel_evaluator.py`):
   - Builds schema properties index (schema_id → property set)
   - Used for static validation of CEL expressions against closed property sets

4. **AuthorizationChecker** (`src/services/authorization_checker.py`):
   - **CRITICAL**: Maps write paths to schema IDs for RBAC authorization
   - See detailed explanation below

5. **Multiple tests** that create artifacts, validate schemas, etc.

**Conclusion**: SchemaMapper is necessary and actively used. The refactoring from `load()` → `load_raw()` was correct — schemas are now pure data (dicts), not reified class instances.

---

## Why AuthorizationChecker Needs SchemaMapper

**The Core Purpose:**

AuthorizationChecker enforces write authorization using **role-based access control (RBAC)** where **resources are artifact schema IDs**.

**How it Works:**

1. **Path → Schema Resolution** (via `resource_for(path)`):
   ```
   Write path: "epics/epic-123.md"
     ↓
   SchemaMapper.load_raw() → {
     "epic": {"x-artifact": {"pathPatterns": ["epics/*.md"], ...}},
     "feature": {"x-artifact": {"pathPatterns": ["*/features/*.md"], ...}},
     ...
   }
     ↓
   Match path against pathPatterns → finds "epic" schema
     ↓
   Resource = "epic" (the schema_id)
   ```

2. **Authorization Check**:
   ```
   Actor writes to "epics/epic-123.md"
     ↓
   resource_for("epics/epic-123.md") → "epic"
     ↓
   Check: does actor have privilege "update_epic"?
     ↓
   Grant or deny based on AuthorizationPolicy
   ```

3. **Disambiguation for Multi-Match Paths**:
   When a path matches multiple schemas (e.g., business vs. enabler variants):
   ```
   Artifact frontmatter field 'type' = "enabler"
     ↓
   Find schema with x-artifact.type == "enabler"
     ↓
   Use that schema's resource (e.g., "feature-enabler")
   ```

**Why This Matters:**

- Without SchemaMapper, AuthorizationChecker can't map paths to resources
- Without resource mapping, RBAC becomes impossible
- Example: can't distinguish write permission for:
  - "epic.md" (business epic) → needs `update_epic` privilege
  - "epic-enabler.md" (enabler epic) → needs `update_epic_enabler` privilege

**Conclusion**: AuthorizationChecker's schema dependency is correct, intentional, and necessary for RBAC to work.

---

## Artifact Reconstitution & Testing

**What We Test:**

New test suite `test_artifact_reconstitution.py` verifies:

1. **`Artifact.to_markdown()`**: Produces byte-stable markdown with preserved structure
2. **`Section.to_markdown()`**: Renders sections + children with correct heading levels
3. **`Section.flatten()`**: Returns depth-first traversal of all sections
4. **`Section.by_name()`**: Finds descendants by name

**Sample Test Flow:**

```python
original = """---
id: story-123
...
---

## Acceptance Criteria
- AC1. Test criterion

## Implementation Notes
### Substep 1
First step.
"""

# Parse
fields = parse_frontmatter(frontmatter(original))
sections = parse_sections(markdown_body(original))
artifact = Artifact(..., sections=sections)

# Reconstitute  
reconstituted = artifact.to_markdown()

# Verify structure preserved
assert "## Acceptance Criteria" in reconstituted
assert "### Substep 1" in reconstituted
assert len(artifact.sections) == 2  # Two top-level sections
assert artifact.sections[1].children[0].name == "Substep 1"
```

**Result**: Artifact is now a proper aggregate root capable of preserving and reconstituting complete markdown structure.

---

## Repository → Mapper Refactoring

**Reason for Rename:**

The classes were misnamed as "Repository" but are actually **data mappers** (model ⇄ filesystem):
- Read YAML/JSON/markdown files → parse into domain objects
- Serialize domain objects → write back to files
- This is the **Data Mapper pattern**, not the **Repository pattern**

**Files Renamed:**
- `artifact_repository.py` → `artifact_mapper.py`
- `schema_repository.py` → `schema_mapper.py`
- `workflow_repository.py` → `workflow_mapper.py`
- `log_repository.py` → `log_mapper.py`

**All references updated** across 31 files (src + tests).

**Tests**: 82 passing (78 existing + 4 new).

---

## __pycache__ Issue

**Status**: RESOLVED

The `__pycache__/` directories are properly ignored in `.gitignore` and are NOT tracked in git. They're created naturally during pytest execution and can be safely ignored.

**Why they keep appearing**:
- Normal side effect of running pytest (Python bytecode caching)
- Not a problem — already ignored by git
- Safe to delete locally if desired: `find . -type d -name __pycache__ -exec rm -rf {} +`

**Resolution**: No action needed — gitignore is correctly configured.
