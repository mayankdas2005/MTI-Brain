/**
 * This mock route is no longer used.
 * The frontend now talks directly to the FastAPI backend via lib/api/.
 * Kept as a placeholder - can be removed entirely.
 */
export async function POST() {
  return Response.json(
    { error: 'This mock endpoint is disabled. Configure NEXT_PUBLIC_API_URL to point to the backend.' },
    { status: 410 },
  );
}
