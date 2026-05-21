/** Global route-level loading state — appears while a server component
 *  segment fetches data. Cheap shimmer placeholder. */
import { GridSkeleton } from "@/components/Skeleton";

export default function Loading() {
  return (
    <div className="space-y-8">
      <div className="card p-8">
        <div className="h-6 w-1/3 rounded-full shimmer mb-3" />
        <div className="h-12 w-2/3 rounded-2xl shimmer mb-2" />
        <div className="h-12 w-1/2 rounded-2xl shimmer" />
      </div>
      <GridSkeleton count={4} />
    </div>
  );
}
