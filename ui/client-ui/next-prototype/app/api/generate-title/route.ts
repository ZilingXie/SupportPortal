import { generateText, Output } from "ai"
import { z } from "zod"

export async function POST(req: Request) {
  const { message }: { message: string } = await req.json()

  const { output } = await generateText({
    model: "openai/gpt-4o",
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
