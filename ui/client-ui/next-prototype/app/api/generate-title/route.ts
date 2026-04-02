import { generateText, Output } from "ai"
import { z } from "zod"

const DEFAULT_MODEL = process.env.PROTOTYPE_OPENAI_MODEL ?? "openai/gpt-5.4"

export async function POST(req: Request) {
  const { message }: { message: string } = await req.json()

  const { output } = await generateText({
    model: DEFAULT_MODEL,
    prompt: `Based on the following IT operations support request, generate a short English session title (no more than 8 words). The title should concisely summarize the core issue.

User message: ${message}`,
    output: Output.object({
      schema: z.object({
        title: z.string(),
      }),
    }),
  })

  return Response.json({ title: output?.title || "IT Support Session" })
}
