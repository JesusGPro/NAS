import unittest
from django.test import TestCase
from drives.views import convert_bytes 
# To test get_disk_stats
from unittest.mock import patch, MagicMock
from drives.views import get_disk_stats

# This simulates the data returned by psutil.disk_partitions
MOCK_PARTITIONS = [
    # 1. A valid partition that should be included
    MagicMock(mountpoint='/', device='/dev/sda1', fstype='ext4'),
    # 2. A partition that should be EXCLUDED by fstype filter
    MagicMock(mountpoint='/sys', device='sysfs', fstype='sysfs'),
    # 3. A partition that should be EXCLUDED by mountpoint filter
    MagicMock(mountpoint='/run/user/1000', device='tmpfs', fstype='tmpfs'),
    # 4. A valid partition that should be included
    MagicMock(mountpoint='/mnt/c', device='/dev/sdb1', fstype='ntfs'),
]

MOCK_USAGE = {
    '/': MagicMock(total=10 * (1024**3), used=5 * (1024**3), free=5 * (1024**3), percent=50.0),
    '/mnt/c': MagicMock(total=100 * (1024**3), used=20 * (1024**3), free=80 * (1024**3), percent=20.0),
}

# This simulates the data returned by psutil.disk_usage
# The total/used/free numbers are for the MOCK_PARTITIONS in the same order
# Data is in bytes (1 GB = 1073741824 bytes)
# MOCK_USAGE_EXCEPTION defined inside function
MOCK_USAGE_EXCEPTION = {
    '/': MagicMock(total=10 * (1024**3), used=5 * (1024**3), free=5 * (1024**3), percent=50.0),
    '/mnt/c': MagicMock(side_effect=OSError("Test Permission Denied")) 
}


class UtilityFunctionTests(TestCase):
    """Tests for utility functions like byte conversion."""

    def test_convert_bytes_basic(self):
        """Test conversion for small, medium, and large byte values."""
        
        # Test 1: Bytes (B)
        self.assertEqual(convert_bytes(100), "100.00 B")
        self.assertEqual(convert_bytes(1023), "1023.00 B")

        # Test 2: Kilobytes (KB)
        self.assertEqual(convert_bytes(1024), "1.00 KB")
        self.assertEqual(convert_bytes(1536), "1.50 KB")

        # Test 3: Megabytes (MB)
        self.assertEqual(convert_bytes(1024**2), "1.00 MB")
        self.assertEqual(convert_bytes(5 * 1024**2), "5.00 MB")

        # Test 4: Gigabytes (GB)
        self.assertEqual(convert_bytes(1024**3), "1.00 GB")
        
        # Test 5: Negative value
        self.assertEqual(convert_bytes(-512), "-512.00 B")

    def test_convert_bytes_terabytes_and_beyond(self):
        """Test the upper bounds (TB and PB)."""
        
        # Test 6: Terabytes (TB)
        tb_value = 1024**4
        self.assertEqual(convert_bytes(tb_value), "1.00 TB")

        # Test 7: Petabytes (PB) - Should hit the final return
        pb_value = 1024**5
        self.assertEqual(convert_bytes(pb_value), "1.00 PB")
        
        # Test 8: Value slightly over PB (should still return PB, rounded)
        self.assertEqual(convert_bytes(pb_value * 2), "2.00 PB")

@patch('drives.views.psutil.disk_usage', side_effect=lambda path: MOCK_USAGE.get(path, MagicMock(total=0)))
@patch('drives.views.psutil.disk_partitions', return_value=MOCK_PARTITIONS)
class DiskStatsTests(TestCase):
    """Tests for get_disk_stats using mocking."""

    def test_get_disk_stats_valid_output(self, mock_partitions, mock_usage):
        """Tests that only valid, non-filtered partitions are returned with correct calculations."""
        mock_usage.side_effect = lambda path: MOCK_USAGE.get(path, MagicMock(total=0))
        disk_stats = get_disk_stats() # Unpack the tuple (disk_data, )

        # 1. Assert filtering: Only 2 partitions should be returned
        self.assertEqual(len(disk_stats), 2)

        # 2. Assert data for the first valid partition ('/')
        # Total: 10 GB, Used: 5 GB, Free: 5 GB, Percent: 50.0
        root_disk = disk_stats[0]
        self.assertEqual(root_disk['mountpoint'], '/')
        self.assertEqual(root_disk['device'], '/dev/sda1')
        self.assertEqual(root_disk['total'], 10.00) # Should be converted to GB
        self.assertEqual(root_disk['used'], 5.00)
        self.assertEqual(root_disk['free'], 5.00)
        self.assertEqual(root_disk['percent'], 50.0)

        # 3. Assert data for the second valid partition ('/mnt/c')
        # Total: 100 GB, Used: 20 GB, Free: 80 GB, Percent: 20.0
        mnt_c_disk = disk_stats[1]
        self.assertEqual(mnt_c_disk['mountpoint'], '/mnt/c')
        self.assertEqual(mnt_c_disk['total'], 100.00) 
        self.assertEqual(mnt_c_disk['used'], 20.00)
        self.assertEqual(mnt_c_disk['free'], 80.00)
        self.assertEqual(mnt_c_disk['percent'], 20.0)

    def test_get_disk_stats_exception_handling(self, mock_partitions, mock_usage):
        """Tests that partitions raising OSError are skipped silently."""
        
        # 1. Setup mock_usage side_effect sequentially.
        # Define the return object for the successful call (for '/')
        good_usage = MagicMock(
            total=10 * (1024**3), 
            used=5 * (1024**3), 
            free=5 * (1024**3), 
            percent=50.0
        )
        # Set the side_effect to return the good data first, then raise the exception
        # This ensures the first partition succeeds and the second fails, 
        # allowing your exception handling to be tested.
        mock_usage.side_effect = [
            good_usage, 
            OSError("Test Permission Denied")
        ]
        
        # 2. Act
        disk_stats = get_disk_stats()
        
        # 3. Assert: Only the successful partition ('/') should be returned
        self.assertEqual(len(disk_stats), 1)
        self.assertEqual(disk_stats[0]['mountpoint'], '/')