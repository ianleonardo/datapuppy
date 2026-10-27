# **Functional Specification: AI-Powered SQL Assistant & RAG Engine**

# **Document Control & Overview**

| Attribute | Specification Details |
| :---- | :---- |
| System Name | AI-Powered SQL Assistant & Retrieval-Augmented Generation (RAG) Engine |
| Architecture Pattern | Vector-search augmented LLM with direct relational database integration |
| Security Paradigm | Role-Based Access Control (RBAC) with user-level vector space isolation |
| Deployment Scope | Enterprise Internal Data Engineering & Analytics Infrastructure |

---

# **1\. User Roles & Access Control**

Access to the system is governed by a strict Role-Based Access Control (RBAC) model to ensure database integrity, prevent unauthorized data access, and maintain operational isolation between administrative functions and analysis activities.

| Feature / Capability | System Administrator | Analyst |
| :---- | :---- | :---- |
| Database Connection Management | Full (Create, Read, Update, Delete) | None |
| Schema Crawling & Pre-definition | Full (Crawl, Edit, Mask, Lock) | Read-Only (View approved structures) |
| Global RAG Knowledge Base | Full Upload & Vector Index Management | Read-Only (Applied to query context) |
| Private RAG Knowledge Base | System-level visibility only | Full Upload, Manage, & Scope to Self |
| Chat History Visibility | Audit metadata only | Full access to owned chat sessions |
| System Audit Log Access | Full Read & Export | None |
| Data Purge Operations | Full Execution | None |
| SQL Query Execution | Native & AI-generated | Native & AI-generated |

## **1.1 System Administrator**

The System Administrator role possesses full technical configuration privileges across the platform.

* **Database Infrastructure Management:** Define, update, test, and remove relational database connection strings.  
* **Schema Governance:** Trigger automatic schema discoveries, pre-define functional entity relationships, apply custom alias names to technical tables, and explicitly exclude sensitive or redundant database tables.  
* **Global Vector Store Operations:** Manage organizational knowledge assets (e.g., global data dictionaries, enterprise ERDs, corporate SQL guidelines) that apply universally across all operational queries.  
* **Governance & Compliance:** Access real-time and historical system activity logs, monitor execution metrics, and execute scheduled or on-demand data purge actions.

## **1.2 Analyst**

The Analyst role represents the primary operational user of the platform, focused on insights extraction, query generation, and interactive data analysis.

* **Natural Language to SQL Execution:** Interface with the AI engine to translate business questions into optimized, dialect-specific SQL.  
* **Manual Query Authoring:** Write, refine, and execute manual SQL queries with automated AI-driven pre-flight verification.  
* **Isolated Vector Workspace:** Upload private context files (such as local business metrics summaries or ad-hoc CSV mappings) that enrich the AI context without exposing data to other platform users.  
* **Session Management:** Create, retain, search, and delete localized chat histories and analytical workflows.

---

# **2\. Functional Modules**

\+-----------------------------------------------------------------------------------+

\|                                  USER INTERFACE                                   \|

\|  \+---------------------------------------+-------------------------------------+  \|

\|  \|           LEFT-HAND PANEL             \|           RIGHT-HAND PANEL          \|  \|

\|  \|  \+---------------------------------+  \|  \+-------------------------------+  \|  \|

\|  \|  \|      RAG & Document Upload      \|  \|  \|    Mermaid Schema Visualizer  \|  \|  \|

\|  \|  \+---------------------------------+  \|  \+-------------------------------+  \|  \|

\|  \|  \|      Rich-Text Chat Editor      \|  \|  \|    Paginated Result DataGrid  \|  \|  \|

\|  \|  \+---------------------------------+  \|  \+-------------------------------+  \|  \|

\|  \+---------------------------------------+-------------------------------------+  \|

\+-----------------------------------------------------------------------------------+

                                         \|

                                         v

\+-----------------------------------------------------------------------------------+

\|                                PROCESSING ENGINE                                  \|

\|  \+------------------+  \+-------------------+  \+--------------------------------+  \|

\|  \| Global Vector DB \|  \| User Vector DB    \|  \| Database Connectors            \|  \|

\|  \| (Shared Context) \|  \| (Isolated Scope)  \|  \| (Oracle, Maria, Postgres, Duck)\|  \|

\|  \+------------------+  \+-------------------+  \+--------------------------------+  \|

\|  \+-----------------------------------------------------------------------------+  \|

\|  \| Safeguards Engine: Pre-flight Check, Size Intercept (COUNT/Metadata), Audit \|  \|

\|  \+-----------------------------------------------------------------------------+  \|

\+-----------------------------------------------------------------------------------+

## **2.1 RAG & Document Management**

The system incorporates a hybrid Vector Indexing Architecture that balances centralized data governance with localized user autonomy.                  \+-----------------------------------+

                  \|        Uploaded Document          \|

                  \+-----------------------------------+

                                    \|

                                    v

                  \+-----------------------------------+

                  \|  Format & Size Validation Check   \|

                  \+-----------------------------------+

                                    \|

            \+-----------------------+-----------------------+

            \|                                               \|

            v                                               v

  \[ System Administrator \]                        \[ Analyst User \]

            \|                                               \|

            v                                               v

\+-----------------------+                       \+-----------------------+

\| Global Vector Scope   \|                       \| Private Vector Scope  \|

\| (Target: All Users)   \|                       \| (Target: Owner Only)  \|

\+-----------------------+                       \+-----------------------+

### **2.1.1 Global References (Admin Managed)**

* **Functional Scope:** Master references, including corporate data lineage docs, standardized metric definitions, and enterprise ERDs.  
* **Indexing Behavior:** Documents uploaded by Administrators are chunked, embedded via vector models, and placed into the `global_knowledge_base` namespace.  
* **Query Lifecycle:** Every user query automatically queries the global vector store to extract relevant contextual metadata prior to generating SQL.

### **2.1.2 Private References (Analyst Managed)**

* **Functional Scope:** User-specific references, such as ad-hoc mapping tables, local business rule definitions, and team notes.  
* **Indexing Behavior:** Vectors created from Analyst uploads are tagged with a mandatory security predicate: `user_id == current_session_user_id`.  
* **Query Lifecycle:** During vector retrieval, a hard filter isolates search queries strictly to the active user's namespace alongside the global namespace.

### **2.1.3 Validation Engine**

All file uploads must pass strict automated validation pipelines prior to ingestion and vector processing:\[Upload Request\] \-\> \[File Format Check\] \-\> \[File Size Check (\<=50MB)\] \-\> \[Text Extraction & Chunking\] \-\> \[Vector Embedding\]

* **Supported Formats:** Portable Document Format (`.pdf`), Microsoft Word (`.docx`), Comma-Separated Values (`.csv`), Plain Text (`.txt`).  
* **Maximum File Size Limit:** 50 MB per individual file upload.  
* **Error Handling:** Files exceeding size thresholds or failing format verification must immediately terminate the upload pipeline and return explicit UI warnings detailing the rejection reason.

---

## **2.2 Database Connection & Schema Management (Admin Only)**

\[Trigger Crawl\] \-\> \[Inspect Metadata Catalog\] \-\> \[Auto-Classify Tables\] \-\> \[Apply Exclusions/Aliases\] \-\> \[Save Lock State\]

### **2.2.1 Heterogeneous Connection Framework**

The platform must support native, connection-pooled drivers for four primary database engines:

* **Oracle Database:** Support for standard TNS connections and Oracle Wallet configurations.  
* **MariaDB:** MySQL-compatible wire protocol with native driver optimization.  
* **PostgreSQL:** Native PostgreSQL driver supporting schema path definitions and standard connection pooling.  
* **DuckDB:** In-memory and file-based DuckDB instance handling for ultra-fast localized columnar operations.

### **2.2.2 Automated Schema Discovery & Administrative Overrides**

* **Automated Crawling:** Admins can trigger metadata discovery to read relational catalogs, foreign key constraints, primary keys, and data types.  
* **Pattern Classification:** Automated flagging of structural anomalies, categorizing target tables into standard classifications (e.g., Fact, Dimension, Staging, Backup).  
* **Administrative Refinement Control:**  
  * **Table Renaming/Aliasing:** Ability to assign human-readable labels to obscure physical table names (e.g., mapping `TBL_CUST_MGR_01` to `Customer Managers`).  
  * **Exclusion Rules:** System configuration to automatically flag and mask "smelly" tables (e.g., tables using suffixes/prefixes like `_BCK`, `_TMP`, `OLD_`, or `TEMP_`) from being ingested into the LLM context.  
  * **Schema Locking:** Once an Admin verifies a schema configuration, it can be "Locked." Locked schemas freeze the system prompt representation to guarantee consistent query output.

---

## **2.3 Intelligent Chat Interface & Privacy**

### **2.3.1 Session Isolation & Storage Architecture**

* **User Multi-Tenancy:** Chat sessions are strictly segmented. Row-Level Security (RLS) policies enforced at the storage layer prevent any session cross-visibility.  
* **Persistence:** All conversations, generated SQL queries, and execution responses are persisted as structured JSON documents mapped directly to the owning `user_id`.

### **2.3.2 Structured Rich-Text Editor & Visualizations**

* **Editor Architecture:** Built on modern block-based editor frameworks (such as Tiptap or BlockNote), allowing seamless integration of text markdown, executable code blocks, and operational chips.  
* **Dynamic Schema Rendering:**  
  * When the LLM explains dynamic table relationships or query execution plans, it natively formats response payloads using `Mermaid.js` syntax.  
  * The chat interface automatically parses `Mermaid.js` blocks and renders interactive Entity-Relationship (ER) diagrams directly inside the chat stream.

---

## **2.4 Query Execution & Data Safeguards**

To prevent analytical queries from causing browser latency, excessive memory utilization, or target database degradation, the system enforces a non-bypassable safeguard pipeline.                                 \[ User Query Execution Triggered \]

                                                 \|

                                                 v

                                 \+-------------------------------+

                                 \|  AI Pre-Flight Syntax Check   \|

                                 \+-------------------------------+

                                                 \|

                                     Pass        v        Fail

                                  \+--------------+--------------+

                                  \|                             \|

                                  v                             v

                   \+----------------------------+   \[ Reject & Return Error \]

                   \| Size Interception Engine   \|

                   \| (Run COUNT / Check Meta)   \|

                   \+----------------------------+

                                  \|

                                  v

                   \+----------------------------+

                   \|  Exceeds 1K Rows/50 Cols?  \|

                   \+----------------------------+

                                  \|

                      Yes         v         No

                   \+--------------+--------------+

                   \|                             \|

                   v                             v

     \[ Display Confirmation Prompt \]   \[ Execute Direct Query \]

                   \|                             \|

         Approved  v                             v

     \+---------------------------------------------------+

     \|    Server-Side Paginated Fetch (50 Rows/Page)     \|

     \+---------------------------------------------------+

### **2.4.1 Intelligent Payload Interception**

* **Pre-Execution Confirmation:** Before any AI-generated SQL query is run against the database, the system must present the drafted query to the user in a readable code block. The user must explicitly click either 'Execute' to run it or 'Refine' to ask the AI for modifications.  
* **Execution Safety Check:** Prior to returning the full dataset to the user interface, the system wraps the target query inside a metadata inspection call or issues a lightweight `SELECT COUNT(*)` wrapper query against the connection.  
* **Interception Thresholds:** If the estimated or counted output exceeds **1,000 total rows** OR **50 total columns**, the execution pipeline pauses. Make these values are configurable.  
* **User Action Prompt:** The system triggers an interactive modal dialog:*"Warning: The requested query returns \[X\] rows and \[Y\] columns. Rendering this large payload may impact browser performance. Do you wish to proceed with paginated streaming?"*

### **2.4.2 Server-Side Pagination**

* **Grid Rendering:** The data grid renders output using server-side offset pagination.  
* **Default Page Limit:** **50 records per page**. Make this value is configurable.  
* Support for pagination and sorting.  
* **Data Transport:** Pages are requested dynamically via API calls (e.g., `LIMIT 50 OFFSET 0`), ensuring large remote datasets never overload client-side memory.

### **2.4.3 AI SQL Pre-Flight Syntax Validation**

* **Static & AI Analysis:** Before manual or modified SQL queries hit the primary database driver, an isolated pre-flight parsing check executes.  
* **Dialect Validation:** The query is verified against the specific Target Dialect rules (e.g., Oracle PL/SQL syntax vs Postgres SQL syntax).  
* **Error Highlighting:** Inline UI annotations mark exact syntax failures, missing aliases, or improper join constraints before physical execution occurs on the target warehouse.  
* **Animated Mascot:** Integrate an animated puppy avatar for the chat interface that displays dynamic emotional expressions (e.g., happy, thinking, confused) based on the context of the conversation.  
* **Conversational Tone:** The AI must adopt a casual, friendly, and approachable tone (like a friend) when asking questions or replying, replacing standard robotic/formal responses.  
* **Clipboard Utility:** Add a one-click 'Copy to Clipboard' button for all chat messages and code blocks.

---

## **2.5 Audit Trail & Purge Capability**

### **2.5.1 System Activity Logging**

The platform maintains a central, immutable audit log capturing all operational events.\[System Event Occurs\] \-\> \[Extract Metadata\] \-\> \[Append to Immutable Log\] \-\> \[Expose to Admin Dashboard\]

* **Logged Attributes:**  
  1. `Timestamp`: ISO 8601 standardized UTC timestamp.  
  2. `User ID`: Unique identifier of the executing user.  
  3. `Action Type`: Explicit categorical flag (`LOGIN`, `DOCUMENT_UPLOAD`, `DB_CONNECTION_CREATE`, `DB_CONNECTION_EDIT`, `QUERY_EXECUTION`, `PURGE_EXECUTED`).  
  4. `Execution Context`: Connection target ID, exact query text (where applicable), or file metadata.  
  5. `Status`: Event outcome (`SUCCESS`, `FAILED`, `INTERCEPTED`).

### **2.5.2 Data Retention & Purge Engine (Admin Only)**

Administrators are provided with a dedicated Purge Management interface to handle legal and security compliance data wipes.\[Select Target: Audit / Chat / Both\] \-\> \[Define Date Range Filter\] \-\> \[Execute Secure Purge Routine\]

* **Purge Scopes:**  
  * *Audit Logs:* Remove system log entries older than a selected threshold.  
  * *User Chat Histories:* Purge conversational records and saved contextual state.  
  * *Combined Purge:* Wipe both audit records and chat logs concurrently.  
* **Range Definition:** Selection via an interactive calendar date range picker (e.g., "Jan 1, 2024 to Mar 31, 2024").  
* **Execution Metrics:** Once complete, the system outputs an audit-compliant confirmation summary detailing total records removed and remaining storage capacities.

---

# **3\. User Interface (UI) & Design System Specifications**

## **3.1 Theme & Styling**

* **Dynamic Playful Theme:** Implement a colorful, playful, and cheerful design system.  
* **Time-Based Transitions:** The theme color palettes must automatically change based on the time of day (Morning, Afternoon, and Night). This theme calculation is evaluated and applied when a user logs in (new session).

## **3.2 Layout & Component Framing**

The core operational dashboard uses a flexible split-pane interface designed for concurrent chat and data inspection.+-----------------------------------------------------------------------------------+

\|                                 APPLICATION NAVBAR                                \|

\+---------------------------------------------------+-------------------------------+

\|                 LEFT PANEL (40%)                  \|       RIGHT PANEL (60%)       \|

\|                                                   \|                               \|

\|  \[ RAG Reference / File Upload Area \]             \|  \[ ER Diagram Visualizer \]    \|

\|  ...............................................  \|  (Mermaid Render Container)   \|

\|                                                   \|                               \|

\|  \[ Interactive Chat Log \]                         \|  ...........................  \|

\|  \- User Messages                                  \|                               \|

\|  \- AI Responses & Code Blocks                     \|  \[ Data Grid Component \]      \|

\|                                                   \|  \- 50 Records per Page        \|

\|  ...............................................  \|  \- Server-Side Pagination     \|

\|                                                   \|                               \|

\|  \[ Rich Text Prompt Editor Input \]                \|                               \|

\+---------------------------------------------------+-------------------------------+

- All frames / sub-window are dockable and resizable.  
- Dropdown background must be sync with theme color style.

---

# **4\. Proposed Pre-Defined Starter Prompts**

To accelerate user onboarding and standard analytical tasks, the chat interface provides interactive standard starter prompt chips:

## **1\. Structure & ERD Discovery**

*"Show me the table structure and relationships for the core module."*

* **Target Engine Action:** Scans active global schema contexts, extracts explicit foreign-key references, and outputs a structured summary accompanied by an auto-rendered Mermaid.js ER diagram.

## **2\. Time-Series Metrics Generation**

*"Generate a SQL query to extract month-over-month growth for Revenue in United States."*

* **Target Engine Action:** Identifies relevant transactional fact tables, applies date-truncation logic for month grouping, builds window functions (`LAG()`), and structures dialect-compliant SQL.

## **3\. Schema Hygiene & Exclusion Check**

*"Are there any temporary or backup tables in this schema that I should ignore?"*

* **Target Engine Action:** Reads active database schema metadata, cross-references internal naming patterns (`_BCK`, `_TMP`, `TEMP`), and details tables that are recommended for exclusion.

## **4\. Query Refactoring & Syntax Verification**

*"Check this manual SQL query for syntax errors and optimize it for Oracle SQL."*

* **Target Engine Action:** Accepts arbitrary user-supplied SQL text, runs static dialect parser checks, highlights syntax anomalies, and returns an optimized version using target dialect standards.

## **5\. Automated Data Insight Extraction**

*"Interpret these query results and summarize the top 3 insights."*

* **Target Engine Action:** Ingests the current active query result set (up to 1,000 rows context limit) and applies analytical summarization logic to highlight anomalies, trends, and key business outcomes.

