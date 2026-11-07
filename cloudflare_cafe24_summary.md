# Cloudflare & CAFE24 Integration - Quick Reference Guide

## Current Architecture
- **Cloudflare Workers** (JavaScript/TypeScript) act as proxy layer
- **Flask Backend** (Python) contains actual CAFE24 logic in `cafe24_gate.py`
- Workers forward requests to Flask backend where fixes are implemented

## Key Findings

### Configuration Issues
1. **Environment Variables**: `BACKEND_URL` must be correctly set in Cloudflare environments
2. **Route Configuration**: Routes may not handle all CAFE24 publishing scenarios
3. **Header Forwarding**: Authentication tokens/cookies might not be properly forwarded

### Integration Challenges
1. **Static Asset Caching**: May conflict with dynamic CAFE24 content
2. **Error Propagation**: Cloudflare Worker error handling may not communicate specific CAFE24 errors
3. **Session Management**: Differences between local and Cloudflare execution contexts

## Recent CAFE24 Fixes (Python Backend)
Implemented in `cafe24_gate.py`:
- Enhanced admin-only board detection with comprehensive pattern matching
- Better access control checks for various restriction patterns  
- Improved logic for publishing block conditions
- Enhanced integration testing for access control behavior

## Critical Issues to Resolve

### 1. Environment Configuration
**Problem**: `BACKEND_URL` not properly configured in Cloudflare environments
**Solution**: Verify and set correct Flask backend URL in production/staging

### 2. Header Forwarding
**Problem**: Authentication tokens/cookies not properly forwarded through proxy
**Solution**: Implement proper header forwarding in index.js

### 3. Caching Conflicts
**Problem**: Static caching interfering with dynamic CAFE24 content
**Solution**: Review and adjust caching logic for CAFE24 responses

### 4. Error Handling
**Problem**: Cloudflare Worker not properly communicating CAFE24-specific errors
**Solution**: Add comprehensive logging and error propagation

## Recommendations

1. **Verify Environment Setup**: Ensure `BACKEND_URL` is correctly configured in all Cloudflare environments
2. **Test Integration Flow**: Validate that Flask backend applies fixes when accessed through proxy
3. **Implement Logging**: Add tracking for request flow from Cloudflare to Flask backend
4. **Monitor Connectivity**: Ensure reliable connection between Cloudflare Workers and Flask backend
5. **Validate Access Control**: Test that recent CAFE24 fixes work properly in deployed environment

## Key Insight
The CAFE24 fixes are implemented in Python files on the Flask backend, not directly in Cloudflare Workers. The proxy layer's main role is to forward requests correctly to enable these fixes.