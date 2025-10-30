# Project Unison – Contributor Setup Checklist

## 1. Prerequisites
Before contributing, ensure your environment includes:
- **Git** (latest version)  
- **Docker Desktop** with **WSL2** backend (Windows) or native Docker (Linux/macOS)  
- **Python 3.10+** and `pip`  
- **Node.js 18+** (for tooling or web components)  
- **VS Code** (recommended) with the following extensions:  
  - *Docker*  
  - *Python*  
  - *YAML*  
  - *Markdown All in One*

---

## 2. Clone the Organization Repositories
```bash
# Example: clone the primary development stack
git clone https://github.com/project-unisonOS/unison-devstack.git
cd unison-devstack
```

Recommended core repos to clone locally:
- `unison-spec`
- `unison-orchestrator`
- `unison-context`
- `unison-storage`
- `unison-devstack`
- `unison-docs`

Keep all under a shared parent folder, e.g.:
```
/git/unison/
```

---

## 3. Build the Local Environment
From inside `unison-devstack`:
```bash
docker compose up --build -d
```
This starts:
- Orchestrator  
- Context service  
- Storage service  

Check containers:
```bash
docker compose ps
```

Verify orchestration:
```bash
curl http://localhost:8080/health
```

---

## 4. Branch and Contribute
Follow the branching model defined in `CONTRIBUTING.md`:
```bash
git checkout -b feature/your-feature-name
```
Commit with clear messages and push to your fork:
```bash
git push origin feature/your-feature-name
```
Open a pull request against the `dev` branch of the target repo.

---

## 5. Accessibility and Documentation
When adding new features or docs:
- Follow **WCAG 2.2 AA** accessibility standards.  
- Include **alt text** for diagrams and screenshots.  
- Keep documentation in Markdown with consistent heading levels.  
- Update `user-journeys` or architectural docs if relevant.

---

## 6. Run Tests
Each core repo will include its own test suite.  
Run with:
```bash
pytest
```
or the local container workflow:
```bash
docker compose run orchestrator pytest
```

---

## 7. Respect Policy and Conduct
All collaboration follows:
- [CONTRIBUTING.md](CONTRIBUTING.md)  
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
