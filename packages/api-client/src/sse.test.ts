import { describe, expect, it } from "vitest";
import { parseSSE } from "./sse";

function stream(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(c) {
      for (const ch of chunks) c.enqueue(enc.encode(ch));
      c.close();
    },
  });
}

describe("parseSSE", () => {
  it("parses events split across chunks", async () => {
    const out = [];
    for await (const e of parseSSE(stream(["event: delta\nda", 'ta: {"t":1}\n\n: ping\n\nevent: done\r\ndata: {"t":2}\r\n\r\n']))) out.push(e);
    expect(out).toEqual([
      { event: "delta", data: '{"t":1}' },
      { event: "done", data: '{"t":2}' },
    ]);
  });
});
