# Security

The current supported release line is 1.x. Upgrade beta installations to the
latest 1.x release after backing up history; see [operations](docs/OPERATIONS.md).

Chipflow is a single-user local tool. Project RTL, testbenches, SDC files, cell
models, and platform Tcl execute through native tools and must be trusted.
The browser server binds to loopback and enforces local Host and same-origin
browser requests. It provides no authentication or multiuser isolation; do not
expose it through a public reverse proxy or bind it to a shared network.
Configuration requests are limited to 1 MiB. Responses prevent embedding and
content-type sniffing and restrict browser resources to the local origin.

Run artifacts and logs may contain proprietary design information. New run
stores and run directories are private to the user. Review artifacts before
sharing them. Setup assessments do not execute suggested installation commands.

Report vulnerabilities using GitHub's private vulnerability reporting on the
repository. If that feature is unavailable, contact the repository maintainer
through their GitHub profile without including exploit details in a public issue.
