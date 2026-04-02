import {
  consumeStream,
  convertToModelMessages,
  streamText,
  type UIMessage,
} from "ai"
import { IT_SUPPORT_SYSTEM_PROMPT } from "@/lib/constants"

export const maxDuration = 30
const DEFAULT_MODEL = process.env.PROTOTYPE_OPENAI_MODEL ?? "openai/gpt-5.4"

export async function POST(req: Request) {
  const { messages }: { messages: UIMessage[] } = await req.json()

  const result = streamText({
    model: DEFAULT_MODEL,
    system: IT_SUPPORT_SYSTEM_PROMPT,
    messages: await convertToModelMessages(messages),
    abortSignal: req.signal,
  })

  return result.toUIMessageStreamResponse({
    originalMessages: messages,
    consumeSseStream: consumeStream,
  })
}
