# Tawk.to + Confluence + Gemini Chatbot
# Ready for Heroku deployment

import os
import requests
import json
from flask import Flask, request, jsonify
import base64
import re
from google import genai
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

class TawkConfluenceBot:
    def __init__(self):
        # Load from environment variables (for Heroku)
        self.confluence_url = os.getenv('CONFLUENCE_URL')
        self.confluence_email = os.getenv('CONFLUENCE_EMAIL')  
        self.confluence_token = os.getenv('CONFLUENCE_TOKEN')
        self.gemini_api_key = os.getenv('GEMINI_API_KEY')
        self.tawk_api_key = os.getenv('TAWK_API_KEY')
        self.tawk_property_id = os.getenv('TAWK_PROPERTY_ID')
        
        # Initialize components
        self.confluence_session = requests.Session()
        self.gemini_client = None
        self.setup_confluence()
        self.setup_gemini()
        
    def setup_confluence(self):
        """Initialize Confluence connection"""
        if self.confluence_url and self.confluence_email and self.confluence_token:
            base_url = f"https://{self.confluence_url}/wiki/rest/api"
            
            auth_string = f"{self.confluence_email}:{self.confluence_token}"
            auth_bytes = base64.b64encode(auth_string.encode()).decode()
            
            self.confluence_session.headers.update({
                'Authorization': f'Basic {auth_bytes}',
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            })
            
            self.confluence_base_url = base_url
            logger.info("Confluence configured successfully")
        else:
            logger.warning("Confluence credentials not provided")
    
    def setup_gemini(self):
        """Initialize Gemini AI"""
        if self.gemini_api_key:
            try:
                self.gemini_client = genai.Client(api_key=self.gemini_api_key)
                logger.info("Gemini AI configured successfully")
            except Exception as e:
                logger.error(f"Gemini setup failed: {e}")
        else:
            logger.warning("Gemini API key not provided")
    
    def search_confluence(self, query):
        """Search Confluence content"""
        try:
            search_strategies = [
                f'text ~ "{query}"',
                f'text ~ {query}',
                f'title ~ "{query}"'
            ]
            
            all_results = []
            
            for cql in search_strategies:
                search_url = f"{self.confluence_base_url}/search"
                params = {
                    'cql': cql,
                    'limit': 3,
                    'expand': 'content.body.storage'
                }
                
                response = self.confluence_session.get(search_url, params=params)
                
                if response.status_code == 200:
                    results = response.json()
                    if results.get('results'):
                        all_results.extend(results['results'])
            
            # Remove duplicates
            unique_results = []
            seen_ids = set()
            
            for result in all_results:
                content_id = result.get('content', {}).get('id')
                if content_id and content_id not in seen_ids:
                    unique_results.append(result)
                    seen_ids.add(content_id)
            
            return unique_results[:3]
            
        except Exception as e:
            logger.error(f"Confluence search error: {e}")
            return []
    
    def extract_clean_text(self, html_content):
        """Extract clean text from HTML"""
        if not html_content:
            return ""
        
        # Remove HTML tags
        text = re.sub('<[^<]+?>', '', html_content)
        
        # Clean up entities
        text = text.replace('&nbsp;', ' ').replace('&amp;', '&')
        text = text.replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&quot;', '"').replace('&#39;', "'")
        
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text
    
    def generate_response(self, query, confluence_results):
        """Generate AI response using Gemini"""
        if not confluence_results:
            return "I couldn't find information about that topic in the knowledge base. Could you try rephrasing your question?"
        
        # Try AI response first
        if self.gemini_client:
            try:
                # Prepare context
                context_parts = []
                
                for result in confluence_results[:2]:  # Use top 2 results
                    title = result.get('title', 'Untitled')
                    content = result.get('content', {})
                    body = content.get('body', {})
                    storage = body.get('storage', {})
                    html_content = storage.get('value', '')
                    
                    clean_text = self.extract_clean_text(html_content)
                    
                    if clean_text:
                        preview = clean_text[:600] + "..." if len(clean_text) > 600 else clean_text
                        context_parts.append(f"**{title}**\n{preview}")
                
                context = "\n\n".join(context_parts)
                
                # Create AI prompt
                prompt = f"""You are a helpful AI assistant answering questions based on documentation.

User's question: "{query}"

Relevant information from the knowledge base:
{context}

Please provide a clear, helpful response based on this information. Be conversational and friendly, like a knowledgeable colleague helping out. If the information doesn't fully answer the question, say so and suggest what additional information might be needed.

Keep your response concise but informative."""

                # Get AI response
                response = self.gemini_client.models.generate_content(
                    model="gemini-1.5-flash",
                    contents=prompt
                )
                
                if response and response.text:
                    return response.text
                    
            except Exception as e:
                logger.error(f"AI generation error: {e}")
        
        # Fallback to basic response
        return self.format_basic_response(query, confluence_results)
    
    def format_basic_response(self, query, confluence_results):
        """Fallback response formatting"""
        if not confluence_results:
            return f"I couldn't find information about '{query}' in the knowledge base."
        
        response_parts = [f"I found {len(confluence_results)} result(s) about '{query}':\n"]
        
        for i, result in enumerate(confluence_results, 1):
            title = result.get('title', 'Untitled')
            content = result.get('content', {})
            body = content.get('body', {})
            storage = body.get('storage', {})
            html_content = storage.get('value', '')
            
            clean_text = self.extract_clean_text(html_content)
            preview = clean_text[:200] + "..." if len(clean_text) > 200 else clean_text
            
            response_parts.append(f"{i}. **{title}**")
            if preview:
                response_parts.append(f"   {preview}")
            response_parts.append("")
        
        return "\n".join(response_parts)
    
    def send_tawk_message(self, chat_id, message):
        """Send message back to Tawk.to chat"""
        if not self.tawk_api_key or not self.tawk_property_id:
            logger.error("Tawk.to credentials not configured")
            return False
        
        try:
            url = f"https://api.tawk.to/v3/chats/{chat_id}/messages"
            
            headers = {
                'Authorization': f'Bearer {self.tawk_api_key}',
                'Content-Type': 'application/json'
            }
            
            payload = {
                'message': message,
                'type': 'msg'
            }
            
            response = requests.post(url, headers=headers, json=payload)
            
            if response.status_code in [200, 201]:
                logger.info(f"Message sent successfully to chat {chat_id}")
                return True
            else:
                logger.error(f"Failed to send message: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending Tawk message: {e}")
            return False

# Initialize bot
bot = TawkConfluenceBot()

@app.route('/')
def home():
    """Health check endpoint"""
    return jsonify({
        'status': 'running',
        'message': 'Tawk.to Confluence Chatbot is running!',
        'confluence_configured': bool(bot.confluence_base_url),
        'gemini_configured': bool(bot.gemini_client),
        'tawk_configured': bool(bot.tawk_api_key and bot.tawk_property_id)
    })

@app.route('/tawk-webhook', methods=['POST'])
def tawk_webhook():
    """Handle incoming webhooks from Tawk.to"""
    try:
        # Get webhook data
        data = request.get_json()
        logger.info(f"Received webhook: {json.dumps(data, indent=2)}")
        
        # Check if this is a customer message
        if data.get('type') == 'chat:start':
            # New chat started - send welcome message
            chat_id = data.get('chatId')
            
            welcome_message = ("👋 Hi! I'm your AI assistant. I can help you find information from our knowledge base. "
                             "Ask me anything!")
            
            bot.send_tawk_message(chat_id, welcome_message)
            
        elif data.get('type') == 'chat:message':
            # New message received
            chat_id = data.get('chatId')
            message = data.get('message', {})
            message_text = message.get('text', '').strip()
            sender_type = message.get('type', '')
            
            # Only respond to visitor messages (not agent messages)
            if sender_type == 'visitor' and message_text:
                logger.info(f"Processing message: {message_text}")
                
                # Search Confluence and generate response
                confluence_results = bot.search_confluence(message_text)
                response = bot.generate_response(message_text, confluence_results)
                
                # Send response back
                bot.send_tawk_message(chat_id, response)
        
        return jsonify({'status': 'success'})
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/test-search', methods=['POST'])
def test_search():
    """Test endpoint for debugging"""
    try:
        data = request.get_json()
        query = data.get('query', '')
        
        if not query:
            return jsonify({'error': 'No query provided'}), 400
        
        # Search and generate response
        confluence_results = bot.search_confluence(query)
        response = bot.generate_response(query, confluence_results)
        
        return jsonify({
            'query': query,
            'confluence_results_count': len(confluence_results),
            'response': response
        })
        
    except Exception as e:
        logger.error(f"Test search error: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)