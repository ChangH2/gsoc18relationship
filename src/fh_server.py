import time
import grpc
import argparse
import pickle

from concurrent import futures
from datetime import datetime
from cassandra.cluster import Cluster
from cassandra.auth import PlainTextAuthProvider
from feedhandling import feed_handling_pb2
from feedhandling import feed_handling_pb2_grpc
from tflearning import tf_learning_pb2
from tflearning import tf_learning_pb2_grpc

_ONE_DAY_IN_SECONDS = 60 * 60 * 24

def connect_to_storage(auth_username, auth_password, cluster_ip, cluster_port):
    auth_provider = PlainTextAuthProvider(username=auth_username, password=auth_password)
    cluster = Cluster(cluster_ip, port=cluster_port, auth_provider=auth_provider)
    session = cluster.connect()
    session.set_keyspace('gsoc3')

    return session

def connect_to_tfl_server(tfl_addr):
    channel = grpc.insecure_channel(tfl_addr)
    return tf_learning_pb2_grpc.TFLearningStub(channel)

def get_training_data_from_storage(auth_username, auth_password, cluster_ip, cluster_port):
    session = connect_to_storage(auth_username, auth_password, cluster_ip, cluster_port)

    return session.execute('SELECT * FROM preprocessing_objects')

def get_features_from_storage(sha256, auth_username, auth_password, cluster_ip, cluster_port):
    session = connect_to_storage(auth_username, auth_password, cluster_ip, cluster_port)

    return session.execute('SELECT * FROM preprocessing_results where sha256=\'' + sha256 + '\' and service_name=\'peinfo\'')

def is_new_data(timestamp):
    with pickle.load(open('checkpoint.p', 'rb')) as checkpoint:
        if timestamp > checkpoint:
            return True
        return False

def update_checkpoint(latest_timestamp):
    with open('checkpoint.p', 'wb') as checkpoint:
        pickle.dump(latest_timestamp, checkpoint)


class FeedHandlingServicer(feed_handling_pb2_grpc.FeedHandlingServicer):
    def __init__(self, args):
        self.verbose = args.verbose
        self.tfl_addr = args.tfl_addr
        self.cluster_ip = args.cluster_ip
        self.cluster_port = args.cluster_port
        self.auth_username = args.auth_username
        self.auth_password = args.auth_password

    def QueryRelationship(self, request, context):
        if self.verbose:
            print('[Request] QueryRelationship()')
            print('[Info] Query the relationship tree')

        stub = connect_to_tfl_server(self.tfl_addr)
        relationships = stub.GetRelationships(tf_learning_pb2.Query(sha256=request.sha256))

        i = 0
        for r in relationships:
            timestamp = 0

            try:
                features = get_features_from_storage(r.sha256, self.auth_username, self.auth_password, self.cluster_ip, self.cluster_port)[0].features
                timestamp = str(datetime.fromtimestamp(float(features[16])))
            except:
                pass

            yield feed_handling_pb2.Relationships(sha256=r.sha256, labels=r.labels, distance=r.distance, features=timestamp)

            i += 1
            if i == 20:
                break

        if self.verbose:
            print('[Info] Relationship sent!')

    def SendMalwareSample(self, request, context):
        pass

    def InitiateTraining(self, request, context):
        if self.verbose:
            print('[Request] InitiateTraining()')
            print('[Info] Initiate training the learning model')

        stub = connect_to_tfl_server(self.tfl_addr)
        stub.TrainModel(tf_learning_pb2.Empty())

        if self.verbose:
            print('[Info] Training done!')

        return feed_handling_pb2.Empty()

    def GetTrainingData(self, request, context):
        if self.verbose:
            print('[Request] GetTrainingData()')
            print('[Info] Start fetching training data from storage')

        rows = get_training_data_from_storage(self.auth_username, self.auth_password, self.cluster_ip, self.cluster_port)

        if self.verbose:
            print('[Info] Training data fetched!')
            print('[Info] Start sending training data')

        latest_timestamp = 0

        for r in rows:
            if is_new_data(r.timestamp):
                if r.timestamp > latest_timestamp:
                    latest_timestamp = r.timestamp

                yield feed_handling_pb2.TrainingData(sha256=r.sha256,
                       features_cuckoo=r.features_cuckoo,
                       features_objdump=r.features_objdump,
                       features_peinfo=r.features_peinfo,
                       features_richheader=r.features_richheader,
                       labels=r.labels)

        if latest_timestamp != 0:
            update_checkpoint(latest_timestamp)

        if self.verbose:
            print('[Info] Training data sent!')

    def SendRelationship(self, request, context):
        pass

    def Echo(self, request, context):
        if self.verbose:
            print('[Request] Echo(%s)' % request.msg)

        return feed_handling_pb2.Foo(msg=request.msg)

def serve(args):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    feed_handling_pb2_grpc.add_FeedHandlingServicer_to_server(FeedHandlingServicer(args), server)
    server.add_insecure_port('[::]:%s' % args.port)
    server.start()

    if args.verbose:
        print('[Info] Feed handling server init')

    try:
        while True:
            time.sleep(_ONE_DAY_IN_SECONDS)
    except KeyboardInterrupt:
        server.stop(0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog='python fh_server.py', description='Feed handling server')
    parser.add_argument('-v', '--verbose', help='Verbose mode', action='store_true')
    parser.add_argument('-p', '--port', help='Listening port for feed handling server')
    parser.add_argument('--tfl-addr', help='Address of tensorflow learning server')
    parser.add_argument('--cluster-ip', help='IPs of clusters', nargs='*')
    parser.add_argument('--cluster-port', help='Port of clusters')
    parser.add_argument('--auth-username', help='Username for clusters\' authentication')
    parser.add_argument('--auth-password', help='Password for clusters\' authentication')

    args = parser.parse_args()

    serve(args)
