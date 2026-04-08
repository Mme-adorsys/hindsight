import { NextRequest, NextResponse } from "next/server";

const DATAPLANE_URL = process.env.HINDSIGHT_CP_DATAPLANE_API_URL || "http://localhost:8888";
const NCR_TIMEOUT_MS = 5 * 60 * 1000; // 5 minutes — NCR can take a while

export async function POST(request: NextRequest) {
  try {
    const body = await request.json().catch(() => ({}));
    const bankId: string | undefined = body?.bank_id;

    if (!bankId) {
      return NextResponse.json({ error: "bank_id is required" }, { status: 400 });
    }

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), NCR_TIMEOUT_MS);

    try {
      const response = await fetch(
        `${DATAPLANE_URL}/v1/default/banks/${encodeURIComponent(bankId)}/ncr/trigger`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
        }
      );

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        return NextResponse.json(
          { error: data?.detail || `Dataplane returned ${response.status}` },
          { status: response.status }
        );
      }

      return NextResponse.json(data, { status: 200 });
    } finally {
      clearTimeout(timeout);
    }
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      return NextResponse.json(
        { error: "NCR run timed out after 5 minutes — it may still be running in the background" },
        { status: 504 }
      );
    }
    console.error("Error triggering NCR:", error);
    return NextResponse.json({ error: "Failed to trigger NCR" }, { status: 500 });
  }
}
