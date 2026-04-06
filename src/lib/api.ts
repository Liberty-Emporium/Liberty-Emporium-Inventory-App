import { Message } from './types';

const GROQ_API_KEY = process.env.NEXT_PUBLIC_GROQ_API_KEY || '';
const GROQ_BASE_URL = 'https://api.groq.com/openai/v1';

interface GroqMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

interface GroqResponse {
  choices: Array<{
    message: {
      content: string;
    };
  }>;
  usage?: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
}

export async function generateResponse(
  message: string,
  modelId: string,
  conversationHistory: Message[] = []
): Promise<string> {
  if (!GROQ_API_KEY) {
    return 'Error: No Groq API key configured. Please set NEXT_PUBLIC_GROQ_API_KEY in your environment.';
  }

  // Build messages array with history
  const messages: GroqMessage[] = [
    {
      role: 'system',
      content: 'You are a helpful AI assistant. Provide clear, concise, and useful responses.'
    }
  ];

  // Add conversation history (last 10 messages to stay within limits)
  const recentHistory = conversationHistory.slice(-10);
  for (const msg of recentHistory) {
    messages.push({
      role: msg.role === 'assistant' ? 'assistant' : 'user',
      content: msg.content
    });
  }

  // Add current message
  messages.push({ role: 'user', content: message });

  // Map our model IDs to Groq model IDs
  const modelMap: Record<string, string> = {
    llama3: 'meta-llama/llama-3.3-70b-versatile',
    mistral: 'mixtral-8x7b-32768',
    codellama: 'meta-llama/llama-3.1-70b-versatile',
    phi3: 'microsoft/phi-3-mini-128k-instruct',
    gemma: 'google/gemma-7b-it',
    mixtral: 'mixtral-8x7b-32768',
    qwen: 'qwen/qwen3-32b',
  };

  const groqModel = modelMap[modelId] || 'meta-llama/llama-3.3-70b-versatile';

  try {
    const response = await fetch(`${GROQ_BASE_URL}/chat/completions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${GROQ_API_KEY}`,
      },
      body: JSON.stringify({
        model: groqModel,
        messages,
        temperature: 0.7,
        max_completion_tokens: 4096,
        stream: false,
      }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.error?.message || `API error: ${response.status}`);
    }

    const data: GroqResponse = await response.json();
    
    if (data.choices && data.choices.length > 0) {
      return data.choices[0].message.content;
    }
    
    return 'No response generated';
  } catch (error) {
    console.error('Groq API error:', error);
    if (error instanceof Error) {
      return `Error: ${error.message}`;
    }
    return 'An error occurred while generating the response.';
  }
}

export function generateTitle(messages: Message[]): string {
  if (messages.length === 0) return 'New Chat';
  
  const firstMessage = messages[0].content;
  const words = firstMessage.split(' ').slice(0, 5).join(' ');
  return words + (firstMessage.split(' ').length > 5 ? '...' : '');
}
