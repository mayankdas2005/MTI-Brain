/**
 * Mock endpoint disabled — frontend now uses the FastAPI backend directly.
 */
export async function POST() {
  return Response.json(
    { error: 'This mock endpoint is disabled.' },
    { status: 410 },
  );
}
