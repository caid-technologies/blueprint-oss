import BlueprintWorkspace from "../../blueprint-workspace";

type ProjectPageProps = {
  params: {
    projectId: string;
  };
};

export default function ProjectPage({ params }: ProjectPageProps) {
  return <BlueprintWorkspace routeProjectId={params.projectId} />;
}
