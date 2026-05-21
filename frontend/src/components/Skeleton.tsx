export function CardSkeleton() {
  return (
    <div className="card p-4 flex flex-col gap-3 h-[280px]">
      <div className="flex items-center justify-between">
        <div className="h-5 w-20 rounded-full shimmer" />
        <div className="h-5 w-5 rounded-full shimmer" />
      </div>
      <div className="h-32 rounded-lg shimmer" />
      <div className="h-3 w-3/4 rounded-full shimmer" />
      <div className="h-3 w-1/2 rounded-full shimmer" />
      <div className="mt-auto flex items-center justify-between">
        <div className="h-5 w-20 rounded-full shimmer" />
        <div className="h-3 w-10 rounded-full shimmer" />
      </div>
    </div>
  );
}

export function GridSkeleton({ count = 8 }: { count?: number }) {
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      {Array.from({ length: count }).map((_, i) => <CardSkeleton key={i} />)}
    </div>
  );
}
