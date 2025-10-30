# Project Unison – Maintainer Checklist

## 1. Versioning
- Use **semantic versioning** across all repos:  
  `MAJOR.MINOR.PATCH` (e.g., `0.3.1`).  
- Tag releases only from `main`.  
- Update `CHANGELOG.md` before tagging.  
- Example:
  ```bash
  git tag -a v0.3.1 -m "Unison orchestrator 0.3.1"
  git push origin v0.3.1
  ```

---

## 2. Coordinated Updates
When changes span multiple modules:
1. Merge each feature branch into `dev` first.  
2. Run the full `unison-devstack` compose build locally.  
3. Verify health checks and API compatibility.  
4. Once validated, merge `dev` → `main` across all affected repos in this order:
   1. `unison-spec`  
   2. `unison-orchestrator`  
   3. `unison-context`  
   4. `unison-storage`  
   5. `unison-devstack`  
   6. `unison-docs`

---

## 3. Release Notes
Each repo should include a short `CHANGELOG.md` entry:
```
## [v0.3.1] – 2025-10-25
### Added
- Context service memory retention metrics  
### Fixed
- EventEnvelope serialization error
```

---

## 4. Continuous Integration
- All workflows must pass in GitHub Actions before merging.  
- Ensure Docker images build successfully in `unison-devstack`.  
- Delete stale branches after merge.

---

## 5. Documentation Sync
- Update `unison-docs` for any architectural or behavior change.  
- Ensure diagram and schema versions match tagged release versions.

---

## 6. Security and Policy
- Rotate API tokens and SSH keys every 6 months.  
- Review `policy` service rules for consistency with organizational security guidelines.  
- Confirm license headers in all source files.

---

## 7. Community and Governance
- Enforce [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).  
- Acknowledge contributors in release notes.  
- Archive inactive branches after 90 days of inactivity.  
- Maintain at least two maintainers with admin rights per repo.
