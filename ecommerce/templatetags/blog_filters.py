from django import template
from django.utils.safestring import mark_safe
import re

register = template.Library()


@register.filter
def format_paragraphs(value):
    """
    Convert plain text or HTML content into properly formatted paragraphs.
    Handles both plain text and HTML content intelligently.
    """
    try:
        if not value:
            return ""
        
        value_str = str(value)
        
        # Check if content has HTML tags
        has_html_tags = bool(re.search(r'<[^>]+>', value_str))
        
        if has_html_tags:
            # Content has HTML - clean it up and ensure proper formatting
            # Remove empty paragraphs
            value_str = re.sub(r'<p>\s*</p>', '', value_str, flags=re.IGNORECASE)
            # Ensure proper spacing around headings
            value_str = re.sub(r'</h([1-6])>\s*<h([1-6])>', r'</h\1>\n\n<h\2>', value_str, flags=re.IGNORECASE)
            # Ensure spacing after headings
            value_str = re.sub(r'</h([1-6])>', r'</h\1>\n\n', value_str, flags=re.IGNORECASE)
            # Clean up multiple newlines
            value_str = re.sub(r'\n\s*\n\s*\n+', '\n\n', value_str)
            return mark_safe(value_str.strip())
        
        # Plain text - convert to HTML paragraphs
        # First, normalize whitespace
        value_str = re.sub(r'\r\n', '\n', value_str)  # Windows line endings
        value_str = re.sub(r'\r', '\n', value_str)  # Mac line endings
        value_str = value_str.strip()
        
        # Split by double newlines (paragraph breaks) first
        if '\n\n' in value_str or value_str.count('\n') >= 3:
            # Has paragraph breaks - split by them
            paragraphs = re.split(r'\n\s*\n+', value_str)
        else:
            # One big block - split by sentences intelligently
            # Split by sentence endings (. ! ?) followed by space and capital letter
            parts = re.split(r'([.!?])\s+([A-ZА-ЯЁ])', value_str)
            
            if len(parts) > 3:
                # Reconstruct sentences
                sentences = []
                i = 0
                while i < len(parts):
                    if i + 2 < len(parts):
                        # We have: text + punctuation + space + capital letter
                        sentence = parts[i] + parts[i+1] + ' ' + parts[i+2]
                        sentences.append(sentence.strip())
                        i += 3
                    else:
                        # Last part
                        if parts[i].strip():
                            sentences.append(parts[i].strip())
                        i += 1
                
                # Group sentences into paragraphs (2-3 sentences per paragraph)
                paragraphs = []
                current_para = []
                
                for sentence in sentences:
                    sentence = sentence.strip()
                    if not sentence:
                        continue
                    
                    current_para.append(sentence)
                    
                    # Every 2-3 sentences, start a new paragraph
                    # Also break on longer sentences (more than 150 chars)
                    sentence_length = len(sentence)
                    if len(current_para) >= 3 or (len(current_para) >= 2 and sentence_length > 150):
                        paragraphs.append(' '.join(current_para))
                        current_para = []
                
                # Add remaining sentences as last paragraph
                if current_para:
                    paragraphs.append(' '.join(current_para))
            else:
                # Fallback: treat as single paragraph
                paragraphs = [value_str]
        
        formatted_paragraphs = []
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            # Check if paragraph starts with HTML tag
            if para.startswith('<'):
                formatted_paragraphs.append(para)
            else:
                # Handle single newlines within paragraph (convert to <br>)
                lines = [line.strip() for line in para.split('\n') if line.strip()]
                if len(lines) > 1:
                    para_content = '<br>'.join(lines)
                else:
                    para_content = lines[0] if lines else para
                
                formatted_paragraphs.append(f'<p>{para_content}</p>')
        
        return mark_safe('\n\n'.join(formatted_paragraphs))
    except Exception as e:
        # If anything goes wrong, return the original value as safe HTML
        return mark_safe(str(value) if value else "")


@register.filter
def linebreaks_to_paragraphs(value):
    """
    Convert line breaks to paragraphs. Each double line break becomes a new paragraph.
    Single line breaks become <br> tags.
    """
    if not value:
        return ""
    
    # Split by double newlines (paragraph breaks)
    paragraphs = re.split(r'\n\s*\n+', str(value))
    
    formatted = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        
        # Replace single newlines with <br>
        para = para.replace('\n', '<br>')
        formatted.append(f'<p>{para}</p>')
    
    return mark_safe('\n\n'.join(formatted))
