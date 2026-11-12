"""
aws-snapshot-cleanup
Nightly: delete this tool's snapshots older than RETENTION_DAYS, then
snapshot every EBS volume tagged Backup=true.

Safety rule: it only ever deletes snapshots it created itself (tagged
ManagedBy=aws-snapshot-cleanup). Snapshots made by hand or by anything
else are never touched -- and the IAM policy enforces that too.
"""
import os
import datetime
import boto3

ec2 = boto3.client("ec2")

RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "7"))
TAG_KEY = os.environ.get("TAG_KEY", "Backup")
TAG_VALUE = os.environ.get("TAG_VALUE", "true")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
MANAGED_BY = "aws-snapshot-cleanup"


def lambda_handler(event, context):
    # Delete first, then create -- so a fresh snapshot can never be
    # caught by the same run's cleanup.
    deleted = delete_expired_snapshots()
    created = snapshot_tagged_volumes()
    result = {"dry_run": DRY_RUN, "deleted": deleted, "created": created}
    print(result)
    return result


def snapshot_tagged_volumes():
    created = []
    pages = ec2.get_paginator("describe_volumes").paginate(
        Filters=[{"Name": f"tag:{TAG_KEY}", "Values": [TAG_VALUE]}]
    )
    for page in pages:
        for vol in page["Volumes"]:
            vid = vol["VolumeId"]
            if DRY_RUN:
                print(f"[dry run] would snapshot {vid}")
                created.append(f"(dry run) {vid}")
                continue
            snap = ec2.create_snapshot(
                VolumeId=vid,
                Description=f"{MANAGED_BY} nightly backup of {vid}",
                TagSpecifications=[{
                    "ResourceType": "snapshot",
                    "Tags": [
                        {"Key": "ManagedBy", "Value": MANAGED_BY},
                        {"Key": "SourceVolume", "Value": vid},
                    ],
                }],
            )
            created.append(snap["SnapshotId"])
    return created


def delete_expired_snapshots():
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=RETENTION_DAYS)
    deleted = []
    pages = ec2.get_paginator("describe_snapshots").paginate(
        OwnerIds=["self"],
        Filters=[{"Name": "tag:ManagedBy", "Values": [MANAGED_BY]}],
    )
    for page in pages:
        for snap in page["Snapshots"]:
            if snap["StartTime"] >= cutoff:
                continue
            sid = snap["SnapshotId"]
            if DRY_RUN:
                print(f"[dry run] would delete {sid} from {snap['StartTime']:%Y-%m-%d %H:%M}")
                deleted.append(f"(dry run) {sid}")
                continue
            ec2.delete_snapshot(SnapshotId=sid)
            deleted.append(sid)
    return deleted
