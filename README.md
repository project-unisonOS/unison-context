# unison-context

The context service manages person state, preferences, and environmental awareness for the Unison platform, providing memory and personalization capabilities.

## Purpose

The context service:
- Stores per-person preferences, accessibility needs, and current task context
- Maintains ephemeral session memory and long-term interaction history
- Provides query, update, and subscription APIs for real-time context access
- Respects privacy zones and consent management
- Enables personalization and context-aware responses across all services

## Current Status

### ✅ Implemented
- FastAPI-based HTTP service with health endpoints
- Person preference storage and retrieval
- Session management with correlation IDs
- Real-time context updates and subscriptions
- Privacy controls and consent management
- Accessibility support and accommodations
- Structured JSON logging for context events
- Integration with orchestrator for context-aware decisions

### 🚧 In Progress
- Advanced personalization algorithms
- Cross-session context persistence
- Environmental sensor integration
- Predictive context suggestions

### 📋 Planned
- Multi-person context sharing (with consent)
- Advanced analytics and insights
- Context export and import capabilities
- Machine learning-based personalization

## Quick Start

### Local Development
```bash
# Clone and setup
git clone https://github.com/project-unisonOS/unison-context
cd unison-context

# Install dependencies
pip install -r requirements.txt

# Run with default configuration
python src/server.py
```

### Docker Deployment
```bash
# Using the development stack
cd ../unison-devstack
docker-compose up -d context

# Health check
curl http://localhost:8081/health
```

## API Reference

### Core Endpoints
- `GET /health` - Service health check
- `GET /ready` - Dependency readiness check
- `GET /context/{person_id}` - Retrieve person context
- `POST /context/{person_id}` - Update person context
- `GET /context/{person_id}/subscribe` - Subscribe to context changes
- `DELETE /context/{person_id}/session/{session_id}` - Clear session data

### Context Operations
```bash
# Get person context
curl -X GET http://localhost:8081/context/person-123 \
  -H "Authorization: Bearer <access-token>"

# Update preferences
curl -X POST http://localhost:8081/context/person-123 \
  -H "Authorization: Bearer <access-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "preferences": {
      "theme": "dark",
      "language": "en",
      "notifications": true
    }
  }'

# Subscribe to changes
curl -X GET http://localhost:8081/context/person-123/subscribe \
  -H "Authorization: Bearer <access-token>"
```

[Full API Documentation](../../unison-docs/developer/api-reference/context.md)

## Configuration

### Environment Variables
```bash
# Service Configuration
CONTEXT_PORT=8081                  # Service port
CONTEXT_HOST=0.0.0.0               # Service host

# Database Configuration
CONTEXT_DATABASE_URL=sqlite:///context.db  # Database connection
CONTEXT_CACHE_TTL=3600             # Cache TTL in seconds

# Privacy and Security
CONTEXT_DEFAULT_RETENTION_DAYS=365  # Data retention period
CONTEXT_CONSENT_REQUIRED=true       # Require consent for data collection
CONTEXT_ENCRYPTION_KEY=your-key     # Encryption key for sensitive data

# Performance
CONTEXT_MAX_SESSIONS=10000         # Maximum concurrent sessions
CONTEXT_QUERY_TIMEOUT=30           # Query timeout in seconds
```

## Context Data Model

### Person Context Structure
```json
{
  "person_id": "person-123",
  "session_id": "session-456",
  "preferences": {
    "theme": "dark",
    "language": "en",
    "notifications": {
      "email": true,
      "push": false,
      "sound": true
    },
    "accessibility": {
      "screen_reader": true,
      "high_contrast": false,
      "font_size": "large"
    }
  },
  "environment": {
    "location": "office",
    "device_type": "desktop",
    "time_zone": "America/New_York",
    "local_time": "2024-01-01T12:00:00Z"
  },
  "activity": {
    "current_task": "document_editing",
    "active_project": "project-789",
    "last_interaction": "2024-01-01T11:45:00Z",
    "session_duration": 1800
  },
  "history": {
    "recent_intents": ["summarize.document", "edit.text", "search.web"],
    "preferred_responses": ["detailed", "visual"],
    "interaction_patterns": {
      "peak_hours": ["09:00", "14:00"],
      "average_session": 45
    }
  }
}
```

## Development

### Setup
```bash
# Install development dependencies
pip install -r requirements-dev.txt

# Run tests
pytest tests/

# Run with debug logging
LOG_LEVEL=DEBUG python src/server.py
```

### Testing
```bash
# Unit tests
pytest tests/unit/

# Integration tests
pytest tests/integration/

# Performance tests
pytest tests/performance/

# Privacy tests
pytest tests/privacy/
```

### Contributing
1. Fork the repository
2. Create a feature branch
3. Make your changes with tests
4. Ensure all tests pass and privacy guidelines are followed
5. Submit a pull request with description

[Development Guide](../../unison-docs/developer/contributing.md)

## Privacy and Security

### Data Protection
- **Encryption**: All sensitive data encrypted at rest
- **Consent Management**: Explicit consent for data collection
- **Data Minimization**: Only collect necessary context information
- **Retention Policies**: Automatic cleanup of old data
- **Access Controls**: Role-based access to context data

### Privacy Zones
Context service respects privacy zones defined in specifications:
- **Private zones**: No context collection without explicit consent
- **Work zones**: Work-related context only during work hours
- **Public zones**: General context for public interactions
- **Sensitive zones**: Enhanced protection for sensitive information

### Compliance
- **GDPR Compliance**: Right to access, rectify, and delete data
- **Data Portability**: Export context data in standard formats
- **Audit Logging**: All context access logged for accountability
- **Privacy by Design**: Privacy considerations in all features

[Privacy Documentation](../../unison-docs/operations/security.md#privacy)

## Architecture

### Context Service Components
```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   API Layer     │───▶│  Business Logic  │───▶│  Data Layer     │
│ (FastAPI)       │    │ (Context Engine) │    │ (Database)      │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                                │
                                ▼
                       ┌──────────────────┐
                       │  Privacy Layer   │
                       │ (Consent & Access)│
                       └──────────────────┘
```

### Data Flow
1. **Request**: Other services request context data
2. **Authentication**: Verify access permissions and consent
3. **Retrieval**: Fetch relevant context from database
4. **Filtering**: Apply privacy and consent rules
5. **Response**: Return filtered context data
6. **Logging**: Log access for audit and compliance

[Architecture Documentation](../../unison-docs/developer/architecture.md)

## Monitoring

### Health Checks
- `/health` - Basic service health
- `/ready` - Database connectivity and readiness
- `/metrics` - Context operation metrics

### Metrics
Key metrics available:
- Context queries per second
- Active session count
- Data storage usage
- Privacy consent status
- Response times by operation

### Logging
Structured JSON logging with correlation IDs:
- Context access and updates
- Privacy consent changes
- Performance metrics
- Error tracking and debugging

[Monitoring Guide](../../unison-docs/operations/monitoring.md)

## Related Services

### Dependencies
- **unison-auth** - Authentication and authorization
- **unison-orchestrator** - Primary context consumer
- **unison-storage** - Long-term context backup

### Consumers
- **unison-orchestrator** - Uses context for personalization
- **unison-inference** - Leverages context for better responses
- **unison-policy** - Applies context to policy decisions
- **I/O modules** - Adapt responses based on context

## Troubleshooting

### Common Issues

**Context Not Updating**
```bash
# Check service health
curl http://localhost:8081/health

# Verify database connection
curl http://localhost:8081/ready

# Check logs for update errors
docker-compose logs context | grep "update"
```

**Privacy Consent Issues**
```bash
# Check consent status
curl -X GET http://localhost:8081/context/person-123/consent \
  -H "Authorization: Bearer <token>"

# Update consent settings
curl -X POST http://localhost:8081/context/person-123/consent \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"data_collection": true, "analytics": false}'
```

**Performance Issues**
```bash
# Check metrics
curl http://localhost:8081/metrics

# Monitor query performance
docker-compose logs context | grep "slow_query"
```

### Debug Mode
```bash
# Enable verbose logging
LOG_LEVEL=DEBUG CONTEXT_DEBUG_QUERIES=true python src/server.py

# Monitor context operations
docker-compose logs -f context | jq '.'
```

[Troubleshooting Guide](../../unison-docs/people/troubleshooting.md)

## Version Compatibility

| Context Version | Unison Common | Auth Service | Minimum Docker |
|-----------------|---------------|--------------|----------------|
| 1.0.0           | 1.0.0         | 1.0.0        | 20.10+         |
| 0.9.x           | 0.9.x         | 0.9.x        | 20.04+         |

[Compatibility Matrix](../../unison-spec/specs/version-compatibility.md)

## License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.

## Support

- **Documentation**: [Project Unison Docs](https://github.com/project-unisonOS/unison-docs)
- **Issues**: [GitHub Issues](https://github.com/project-unisonOS/unison-context/issues)
- **Discussions**: [GitHub Discussions](https://github.com/project-unisonOS/unison-context/discussions)
- **Security**: Report security issues to security@unisonos.org
