import CreateProjectModal from '../../src/components/CreateProjectModal';
import type { Project } from '../../src/api/projects';

export default function CreateProjectDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (project: Project) => void;
}) {
  return <CreateProjectModal open onCancel={onClose} onSuccess={onCreated} />;
}
