Seems like the pods reference each other in dependencies by "pod_id" which is a name

3002 - artimis 
3003 - hydroponics
3004 - aquifer

supplies means what the pod supplies to other pods, and the resource is what it is

read during exec
```
docker compose exec rover sh -lc 'ls -l /rover/output && tail -n 200 /rover/output/.map.log'
```
read output 
```
docker compose exec rover sh -lc 'python -m json.tool /rover/output/map.json'
```
