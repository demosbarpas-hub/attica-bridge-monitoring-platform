# Attica Region Bridge Monitoring Platform

## Overview
A full-stack geospatial web platform deployed on a local intranet server to manage and monitor civil infrastructure data for the Region of Attica (Περιφέρεια Αττικής). This independent project digitizes the tracking, documentation, and geographical mapping of regional bridges into a centralized local database.

The system runs on a dedicated local server with a static IP, providing a lightweight, high-performance interface for civil engineers to manage structural metadata, blueprints, and inspection reports without relying on external cloud infrastructure.

**Author:** Demosthenes Karamparpas  
**Deployment:** Independent Project for the Region of Attica

## System Architecture & Core Features
*   **Geospatial Mapping (Leaflet.js):** Dynamic interactive maps mapping bridge infrastructure. Includes a custom Python coordinate transformer (`pyproj`) to accurately convert Greek Grid **EGSA '87 (EPSG:2100)** geospatial coordinates into standard **WGS84 (EPSG:4326)** coordinates on the fly for web rendering.
*   **In-Browser Document Rendering:** Bridge inspection PDFs and structural plans open directly within sub-windows (iframes) inside the application, preventing the need for local downloads and keeping the workflow seamless.
*   **Streamlined Access Control:** Secure authentication flow separating public 'Guest' viewing access from 'Admin' capabilities using a hardcoded master passphrase, eliminating the need for complex user account management on a local network.
*   **Automated Database Backups:** Integrated a one-click automated bash script executed directly from the Python backend to backup the MySQL database to a separate local disk.
*   **Asset Management:** Secure file upload and routing systems handling structural images and architectural documents. Includes automated localized filename slugification and collision prevention.

## Tech Stack
*   **Backend:** Python, Flask, MySQL (`mysql.connector`), `pyproj`, `rapidfuzz`
*   **Frontend:** HTML5, CSS3, Bootstrap 5, Jinja2 Templating
*   **Mapping:** Leaflet.js, OpenStreetMap
*   **Environment:** Local Intranet Server (Static IP)

## Directory Structure
*   `app.py` - Core Flask application, routing, coordinate conversion, and database execution logic
*   `requirements.txt` - Python dependency list
*   `templates/` - Jinja2 HTML templates (`map.html`, `bridge_details.html`, `search.html`, etc.)
*   `static/` - Custom CSS stylesheets and background assets
*   `backup.sh` - (GitIgnored) Shell script for automated MySQL database backups
