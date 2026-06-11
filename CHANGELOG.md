# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-06-11
### Fixed
- **Security:** Locked down CORS configuration (SEC-01).
- **Security:** Prevented path traversal in file uploads by using UUID-based filenames (SEC-02).
- **Security:** Added authentication to heartbeat endpoint (SEC-03).
- **Security:** Added magic-byte validation and size limits to avatar uploads (SEC-04, SEC-14).
- **Security:** Added basic SSRF protection for federation peers (SEC-13).
- **Performance:** Implemented FFmpeg semaphore to limit concurrent transcoding jobs (SEC-11).
- **Architecture:** Refactored monolithic `agents.py` by extracting `identity` and `analytics` routers.
- **Code Quality:** Added missing database indexes for performance.
- **Code Quality:** Cleaned up inline imports to improve maintainability.
- **Documentation:** Added missing documentation for Guilds, TROs, and Platform Weather.
