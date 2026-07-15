"""
Content Extractor Module
Handles extraction of text content from files (PDF, TXT, DOCX) and webpages.
"""

import os
import requests
from bs4 import BeautifulSoup
from PyPDF2 import PdfReader
from docx import Document
from urllib.parse import urlparse


def extract_text_from_pdf(file_path):
    """
    Extract text from a PDF file.

    Args:
        file_path (str): Path to the PDF file

    Returns:
        str: Extracted text content
    """
    try:
        text = ""
        with open(file_path, 'rb') as file:
            pdf_reader = PdfReader(file)
            for page in pdf_reader.pages:
                text += page.extract_text()
        return text.strip()
    except Exception as e:
        raise Exception(f"Error extracting text from PDF: {str(e)}")


def extract_text_from_docx(file_path):
    """
    Extract text from a DOCX file.

    Args:
        file_path (str): Path to the DOCX file

    Returns:
        str: Extracted text content
    """
    try:
        doc = Document(file_path)
        text = "\n".join([para.text for para in doc.paragraphs])
        return text.strip()
    except Exception as e:
        raise Exception(f"Error extracting text from DOCX: {str(e)}")


def extract_text_from_txt(file_path):
    """
    Extract text from a TXT file.

    Args:
        file_path (str): Path to the TXT file

    Returns:
        str: Extracted text content
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            text = file.read()
        return text.strip()
    except Exception as e:
        raise Exception(f"Error extracting text from TXT: {str(e)}")


def extract_text_from_file(file_path):
    """
    Automatically detect file type and extract text.

    Args:
        file_path (str): Path to the file

    Returns:
        str: Extracted text content
    """
    _, file_extension = os.path.splitext(file_path)
    file_extension = file_extension.lower()

    if file_extension == '.pdf':
        return extract_text_from_pdf(file_path)
    elif file_extension == '.docx':
        return extract_text_from_docx(file_path)
    elif file_extension == '.txt':
        return extract_text_from_txt(file_path)
    else:
        raise ValueError(f"Unsupported file type: {file_extension}")


def extract_text_from_url(url):
    """
    Extract and clean text content from a webpage.

    Args:
        url (str): URL of the webpage

    Returns:
        str: Extracted and cleaned text content
    """
    try:
        # Validate URL
        parsed_url = urlparse(url)
        if not parsed_url.scheme:
            url = 'https://' + url

        # Fetch the webpage
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # Parse HTML and extract text
        soup = BeautifulSoup(response.content, 'html.parser')

        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()

        # Remove navigation and footer elements
        for element in soup(["nav", "footer", "header"]):
            element.decompose()

        # Get text
        text = soup.get_text()

        # Clean up whitespace
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = '\n'.join(chunk for chunk in chunks if chunk)

        return text.strip()
    except requests.exceptions.RequestException as e:
        raise Exception(f"Error fetching URL: {str(e)}")
    except Exception as e:
        raise Exception(f"Error extracting text from webpage: {str(e)}")


def validate_content_length(content, min_length=100, max_length=1000000):
    """
    Validate that the extracted content meets length requirements.

    Args:
        content (str): The content to validate
        min_length (int): Minimum required length
        max_length (int): Maximum allowed length

    Returns:
        bool: True if content is valid

    Raises:
        ValueError: If content doesn't meet requirements
    """
    content_length = len(content)

    if content_length < min_length:
        raise ValueError(f"Content is too short ({content_length} chars). Minimum required: {min_length} chars.")

    if content_length > max_length:
        raise ValueError(f"Content is too long ({content_length} chars). Maximum allowed: {max_length} chars.")

    return True
