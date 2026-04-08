import { NextResponse } from "next/server";

const DATAPLANE_URL = process.env.HINDSIGHT_CP_DATAPLANE_API_URL || "http://localhost:8888";

export async function GET() {
  try {
    const response = await fetch(`${DATAPLANE_URL}/v1/default/config`, {
      cache: "no-store",
    });

    if (!response.ok) {
      throw new Error(`Dataplane returned ${response.status}`);
    }

    const data = await response.json();
    return NextResponse.json(data, { status: 200 });
  } catch (error) {
    console.error("Error fetching system config:", error);
    return NextResponse.json({ error: "Failed to fetch system config" }, { status: 500 });
  }
}
