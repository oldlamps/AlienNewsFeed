import requests
import json

def query_ollama(prompt, model="llama3:latest", timeout=90):
    """
    Sends a prompt to the local Ollama API and yields the streaming response.
    This is now a generator function.
    """
    url = "http://localhost:11434/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True  # Enable streaming in the Ollama request payload
    }
    
    try:
        # The 'stream=True' argument here tells the requests library to handle this as a stream
        with requests.post(url, json=payload, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            
            # Process each line in the streaming response
            for line in response.iter_lines():
                if line:
                    try:
                        # Each line is a separate JSON object
                        data = json.loads(line)
                        
                        # The actual text token is in the 'response' key
                        if 'response' in data:
                            yield data['response']
                        
                        # The 'done' key is True for the final JSON object
                        if data.get('done'):
                            break
                    except json.JSONDecodeError:
                        # In case of a malformed line, just continue
                        continue
                        
    except requests.exceptions.Timeout:
        yield f"\n\n[Error: Request timed out after {timeout} seconds.]"
    except requests.exceptions.ConnectionError:
        yield "\n\n[Error: Connection to Ollama failed. Is Ollama running?]"
    except requests.exceptions.RequestException as e:
        yield f"\n\n[Error: An unexpected error occurred: {e}]"