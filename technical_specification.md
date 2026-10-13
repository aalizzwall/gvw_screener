# Technical Specification: Deepen the Data Provider Refactor

## Overview
The current implementation of the data collection engine is a monolithic "God Module" (`gvw_collector.py`). It suffers from low **locality** by intermingling high-level scheduling logic, low-level API protocol details (TWSE and Yahoo Finance), and local state management (JSON persistence and health tracking). This high coupling creates architectural friction, making it difficult to unit test individual components without triggering side effects like network requests or file I/O.

This refactor aims to restructure the module using a **Provider/Adapter** pattern to increase **depth** and decoupling.

## Goals
- Decouple the orchestrator from specific API implementations.
- Introduce a formal **interface** for data retrieval.
- Improve testability by creating **seams** for dependency injection.
- Enhance maintainability by separating concerns (scheduling vs. fetching vs. storage).

## User Stories
- **As a Developer**, I want to add a new data source (e.g., Bloomberg or Alpha Vantage) without modifying the core scheduling logic.
- **As a Developer**, I want to test the orchestration logic using mock data without making real network calls.
- **As a Developer**, I want to test individual API adapters in isolation to ensure they correctly parse specific vendor formats.
- **As a DevOps Engineer**, I want to easily swap local JSON storage for a database by changing a single implementation detail.
- **As a Data Scientist**, I want to ensure that the fallback logic (TWSE $\rightarrow$ Yahoo Finance) is robust and transparently logged.

## Architecture & Design

### Module Structure
The refactor will introduce a hierarchical module structure:
1.  **Orchestrator Module**: Manages the high-level workflow, timing, and decision-making (e.g., which stocks to update and when).
2.  **Data Provider Interface**: Defines the contract for all data retrieval operations.
3.  **Adapters**: Specialized modules implementing the interface for specific external services.
4.  **State Manager**: Handles the persistence of stock data and health status to local storage.

### Design Principles
- **Interface-Driven**: The orchestrator will only interact with the `DataProvider` interface, effectively creating a **seam** for testing.
- **Adapter Pattern**: `TWSEAdapter` and `YahooAdapter` will encapsulate the nuances of their respective APIs, ensuring the orchestrator remains agnostic of request headers, URL structures, or response formats.
- **Separation of Concerns (SoC)**: High-level logic will reside in a shallow module, while low-level API complexities will be pushed to deeper, specialized modules.
- **Dependency Injection**: The orchestrator will be injected with a concrete `DataProvider` implementation, allowing for easy swapping of providers or mocks during testing.

## Implementation Decisions
- **Decoupling the Orchestrator**: The core logic will be refactored to depend on an abstraction rather than concrete API clients.
- **Adapter Implementation**: Each external API will be encapsulated within its own module, handling all protocol-specific logic (headers, error handling, parsing).
- **State Management Isolation**: Local file I/O and JSON management will be moved to a dedicated persistence layer.
- **Error Handling Strategy**: The fallback mechanism (switching between different adapters) will be abstracted away from the main loop.

## Testing Strategy
- **Unit Testing (Interface Level)**: Test the orchestration logic by injecting a `MockDataProvider`. This verifies the "what" (scheduling, decision-making) without the "how" (network/parsing).
- **Unit Testing (Adapter Level)**: Test each adapter in isolation. Use tools to intercept network requests to verify that the adapter correctly translates internal requests into vendor-specific API calls and correctly parses diverse response formats.
- **Integration Testing**: Verify the full "Orchestrator $\rightarrow$ Adapter $\rightarrow$ Filesystem" flow to ensure the components work together correctly in a real-world scenario.
- **Regression Testing**: Ensure that the fallback logic (TWSE $\rightarrow$ Yahoo Finance) behaves identically to the current implementation.
