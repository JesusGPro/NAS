💾 Raspberry Pi NAS Explorer (Django-Based File Management)

This project is a Network Attached Storage (NAS) simulation and file management application built using the Django framework. It is specifically designed to run on a Raspberry Pi 5 and interface with up to four 1TB physical or simulated hard drives, providing a total of 4TB of accessible, web-managed storage.


🚀 Key Features

The application provides authenticated users with a secure and functional web-based file explorer interface, bringing essential NAS capabilities to the Raspberry Pi environment.

1. Multi-Drive File Explorer

Four Simulated Drives: Manages access and content across four distinct 1TB storage volumes (HardDrive-1 to HardDrive-4).

Intuitive Navigation: Provides a clean, modern interface for browsing directories and files on the drives.

Permission Control: Implements user authentication and a strict permission layer to control who can view (can_view) and modify (can_modify) content in specific locations.

2. Comprehensive File Operations

The system supports all standard file manipulation tasks, including robust bulk operations for efficiency:

Operation

Functionality

Bulk Delete

Allows users to select and delete multiple files and folders in a single action.

Bulk Copy/Cut

Clipboard functionality (using session state) to efficiently copy or move multiple selected items between different directories or drives.

Single Item Ops

Supports renaming, single deletion, and folder creation.

Webpage in English and Spanish

3. Storage Monitoring

Disk Statistics: Provides real-time reports on the storage health of the simulated drives, including total space, used space, free space, and usage percentage.


🛠️ Technology Stack

Backend Framework: Python 3 / Django

Storage Simulation: Standard OS file system manipulation (os, shutil) within a defined root path (NAS_DRIVE_ROOT).

Environment: Optimized for deployment on Raspberry Pi 5.

Dependencies: Uses psutil (simulated in testing) for disk monitoring and standard Django components for views, routing, and user authentication.


⚙️ Installation and Setup

Prerequisites

Raspberry Pi 5 with a working OS (e.g., Raspberry Pi OS).

Python 3 and pip.

Git installed.

1. Clone the Repository

git clone https://github.com/JesusGPro/NAS.git
cd NAS


2. Setup Virtual Environment

# Create and activate the virtual environment
python3 -m venv .venv
source .venv/bin/activate


3. Install Dependencies

pip install -r requirements.txt # (Assuming you have a requirements.txt file)


4. Create Drives and Initialize

The application expects specific directories to exist for the drives:

# Navigate to the root of your project
mkdir HardDrive-1
mkdir HardDrive-2
mkdir HardDrive-3
mkdir HardDrive-4


5. Run Migrations and Server

python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 0.0.0.0:8000


You can now access the NAS Explorer via your Raspberry Pi's IP address on port 8000 (e.g., http://192.168.1.100:8000/).
