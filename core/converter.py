"""
Converter module for MangaForge.

This module handles conversion of downloaded images to CBZ and PDF formats.
It provides shared conversion logic that all providers can use.
"""
import logging
import zipfile
from pathlib import Path
from typing import List, Optional
import shutil

logger = logging.getLogger(__name__)


class Converter:
    """
    Shared conversion logic for all manga providers.

    This class handles PDF/CBZ conversion and cleanup operations.
    It uses PIL for image processing and reportlab for PDF generation.

    Features:
    - Convert images to CBZ (ZIP archive)
    - Convert images to PDF with proper formatting
    - Automatic cleanup of source images after conversion
    - Progress tracking for large conversions
    - Error handling and logging
    """

    def __init__(self):
        """Initialize the converter."""
        self._check_dependencies()

    def _check_dependencies(self):
        """Check if required dependencies are available."""
        try:
            from PIL import Image
            self.PIL_available = True
        except ImportError:
            self.PIL_available = False
            logger.warning("PIL/Pillow not available. PDF conversion will be limited.")

        try:
            import reportlab
            self.reportlab_available = True
        except ImportError:
            self.reportlab_available = False
            logger.warning("ReportLab not available. PDF conversion will not be available.")

    @staticmethod
    def to_cbz(image_dir: Path,
               output_path: Path,
               delete_images: bool = False) -> Path:
        """
        Convert images to CBZ format.

        CBZ is essentially a ZIP file with a .cbz extension containing
        images in the correct reading order.

        Args:
            image_dir: Directory containing images to convert
            output_path: Output path for the CBZ file
            delete_images: Whether to delete source images after conversion

        Returns:
            Path to the created CBZ file

        Raises:
            ConverterError: If conversion fails
        """
        try:
            logger.info(f"Converting {image_dir} to CBZ: {output_path}")

            # Find all image files
            image_extensions = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp')
            image_files = [
                f for f in sorted(image_dir.iterdir())
                if f.is_file() and f.suffix.lower() in image_extensions
            ]

            if not image_files:
                raise ConverterError(f"No image files found in {image_dir}")

            logger.info(f"Found {len(image_files)} images to convert")

            # Create CBZ (ZIP) file
            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as cbz_file:
                for image_file in image_files:
                    # Add file to archive with just the filename (no path)
                    cbz_file.write(image_file, image_file.name)
                    logger.debug(f"Added {image_file.name} to CBZ")

            logger.info(f"Successfully created CBZ: {output_path}")

            # Clean up images if requested
            if delete_images:
                Converter._cleanup_images(image_dir, image_files)
                logger.info(f"Cleaned up source images from {image_dir}")

            return output_path

        except Exception as e:
            logger.error(f"Failed to create CBZ: {e}")
            raise ConverterError(f"CBZ conversion failed: {e}")

    @staticmethod
    def to_pdf(image_dir: Path,
               output_path: Path,
               delete_images: bool = False) -> Path:
        """
        Convert images to PDF format.

        Creates a PDF file with images arranged for optimal reading.
        Uses PIL for image processing and reportlab for PDF generation.

        Args:
            image_dir: Directory containing images to convert
            output_path: Output path for the PDF file
            delete_images: Whether to delete source images after conversion

        Returns:
            Path to the created PDF file

        Raises:
            ConverterError: If conversion fails
        """
        try:
            logger.info(f"Converting {image_dir} to PDF: {output_path}")

            # Check dependencies
            if not Converter._check_pil():
                raise ConverterError("PIL/Pillow is required for PDF conversion")

            if not Converter._check_reportlab():
                raise ConverterError("ReportLab is required for PDF conversion")

            # Find all image files
            image_extensions = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp')
            image_files = [
                f for f in sorted(image_dir.iterdir())
                if f.is_file() and f.suffix.lower() in image_extensions
            ]

            if not image_files:
                raise ConverterError(f"No image files found in {image_dir}")

            logger.info(f"Found {len(image_files)} images to convert")

            # Import here to handle missing dependencies gracefully
            from PIL import Image
            from reportlab.lib.pagesizes import A4, LETTER
            from reportlab.platypus import SimpleDocTemplate, Image as RLImage, PageBreak
            from reportlab.lib.units import inch

            # Create PDF document
            doc = SimpleDocTemplate(str(output_path), pagesize=A4)
            story = []

            for image_file in image_files:
                try:
                    # Open and process image
                    img = Image.open(image_file)

                    # Convert to RGB if necessary
                    if img.mode != 'RGB':
                        img = img.convert('RGB')

                    # Calculate dimensions to fit A4 page with margins
                    page_width, page_height = A4
                    margin = 0.5 * inch

                    # Calculate scaling to fit page
                    img_width, img_height = img.size
                    available_width = page_width - (2 * margin)
                    available_height = page_height - (2 * margin)

                    width_ratio = available_width / img_width
                    height_ratio = available_height / img_height
                    scale = min(width_ratio, height_ratio)

                    if scale < 1.0:
                        new_width = img_width * scale
                        new_height = img_height * scale
                    else:
                        new_width = img_width
                        new_height = img_height

                    # Create reportlab Image object
                    rl_img = RLImage(image_file)
                    rl_img.drawWidth = new_width
                    rl_img.drawHeight = new_height

                    story.append(rl_img)
                    story.append(PageBreak())

                    logger.debug(f"Added {image_file.name} to PDF")

                except Exception as e:
                    logger.warning(f"Failed to process image {image_file}: {e}")
                    continue

            # Build PDF
            doc.build(story)
            logger.info(f"Successfully created PDF: {output_path}")

            # Clean up images if requested
            if delete_images:
                Converter._cleanup_images(image_dir, image_files)
                logger.info(f"Cleaned up source images from {image_dir}")

            return output_path

        except ConverterError:
            raise
        except Exception as e:
            logger.error(f"Failed to create PDF: {e}")
            raise ConverterError(f"PDF conversion failed: {e}")

    @staticmethod
    def _cleanup_images(image_dir: Path, image_files: List[Path]):
        """Clean up image files after conversion."""
        try:
            for image_file in image_files:
                if image_file.exists():
                    image_file.unlink()
                    logger.debug(f"Deleted {image_file}")

            # Try to remove the directory if it's empty
            try:
                if not any(image_dir.iterdir()):
                    image_dir.rmdir()
                    logger.debug(f"Removed empty directory {image_dir}")
            except OSError:
                # Directory not empty or other error, ignore
                pass

        except Exception as e:
            logger.warning(f"Failed to cleanup images: {e}")

    @staticmethod
    def _check_pil() -> bool:
        """Check if PIL is available."""
        try:
            import PIL
            return True
        except ImportError:
            return False

    @staticmethod
    def _check_reportlab() -> bool:
        """Check if ReportLab is available."""
        try:
            import reportlab
            return True
        except ImportError:
            return False


class ConverterError(Exception):
    """Exception raised when conversion fails."""
    pass