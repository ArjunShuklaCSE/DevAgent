import { ReviewView } from "@/components/review/review-view";

export default async function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ReviewView runId={id} />;
}
