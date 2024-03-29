import os

import datasets
import multiprocessing
import numpy as np
import pandas as pd

from joblib import Parallel, delayed
from data_preprocessing import create_s, preprocess
from optimization import CccpClassifier, JointClassifier, OracleClassifier, DccpClassifier, \
    NaiveClassifier, MMClassifier, WeightedClassifier
from optimization.c_estimation import TIcEEstimator, ElkanNotoEstimator
from optimization.metrics import approximation_error, c_error, auc, alpha_error

dir_path = os.path.dirname(os.path.realpath(__file__))

used_datasets = [
    'Adult',
    'BreastCancer',
    'credit-a',
    'credit-g',
    'diabetes',
    'heart-c',
    'spambase',
    'vote',
    'wdbc',
]

first_run_index = 0
total_runs = 100

RESULTS_ROOT_DIR = 'detailed_results'

const_c_classifiers = {
    # 'Naive': NaiveClassifier(TIcEEstimator()),
    'Weighted': WeightedClassifier(TIcEEstimator()),
    'Joint': JointClassifier(),
    'CCCP': CccpClassifier(verbosity=1, tol=1e-4, max_iter=40),
    'MM': MMClassifier(verbosity=1, tol=1e-4, max_iter=40),
    'DCCP': DccpClassifier(tau=1, verbosity=1, tol=1e-3, max_iter=40),
}

joint_classifiers = {
    # 'Naive - TIcE': NaiveClassifier(TIcEEstimator()),
    # 'Naive - EN': NaiveClassifier(ElkanNotoEstimator()),
    'Weighted - TIcE': WeightedClassifier(TIcEEstimator()),
    'Weighted - EN': WeightedClassifier(ElkanNotoEstimator()),
    'Joint': JointClassifier(),
    'CCCP': CccpClassifier(verbosity=1, tol=1e-4, max_iter=40),
    'MM': MMClassifier(verbosity=1, tol=1e-4, max_iter=40),
    'DCCP': DccpClassifier(tau=1, verbosity=1, tol=1e-3, max_iter=40),
}


def oracle_prediction(X_train, y_train, X_test):
    clf = OracleClassifier()
    clf.fit(X_train, y_train)

    y_proba = clf.predict_proba(X_test)
    return y_proba


def pu_prediction(clf, X_train, s_train, X_test, c=None):
    clf.fit(X_train, s_train, c)

    y_proba = clf.predict_proba(X_test)
    return y_proba


def joint_prediction(clf, X_train, s_train, X_test):
    clf.fit(X_train, s_train)

    y_proba = clf.predict_proba(X_test)
    return y_proba, clf.c_estimate


def calculate_metrics(clf, X_train, y_train, s_train, X_test, y_test, c, oracle_pred, const_c: bool = False):
    if const_c:
        y_pred = pu_prediction(clf, X_train, s_train, X_test, c=c)
        c_estimate = None
    else:
        y_pred, c_estimate = joint_prediction(clf, X_train, s_train, X_test)

    approx_err = approximation_error(y_pred, oracle_pred)
    auc_score = auc(y_test, y_pred)

    if const_c:
        return pd.DataFrame({
            'Metric': ['Błąd aproksymacji (AE) prawdopodobieństwa a posteriori',
                       'AUC',
                       'Czas wykonania',
                       'Iteracje metody',
                       'Ewaluacje funkcji w trakcie optymalizacji'],
            'Value': [approx_err,
                      auc_score,
                      clf.total_time,
                      clf.iterations,
                      clf.evaluations]
        })
    else:
        c_err = c_error(c_estimate, c)
        y = np.concatenate([y_train, y_test])
        alpha_err = alpha_error(clf.get_STD_alpha(), y)

        return pd.DataFrame({
            'Metric': ['Błąd aproksymacji (AE) prawdopodobieństwa a posteriori',
                       r'Błąd estymacji częstości etykietowania',
                       r'Błąd estymacji prawdopodobieństwa a priori',
                       'AUC',
                       'Czas wykonania',
                       'Iteracje metody',
                       'Ewaluacje funkcji w trakcie optymalizacji'],
            'Value': [approx_err,
                      c_err,
                      alpha_err,
                      auc_score,
                      clf.total_time,
                      clf.iterations,
                      clf.evaluations]
        })


def get_oracle_metrics(y_test, oracle_pred):
    auc_score = auc(y_test, oracle_pred)

    return pd.DataFrame({
        'Metric': ['AUC'],
        'Value': [auc_score]
    })


def run_test(dataset_name, dataset, target_c, run_number):
    try:
        X, y = dataset

        s, c = create_s(y, target_c)
        X_train, X_test, y_train, y_test, s_train, s_test = preprocess(X, y, s, test_size=0.2)

        if np.sum(y_test == 1) == 0 or np.sum(y_test == 0) == 0 \
                or np.sum(y_train == 1) == 0 or np.sum(y_train == 0) == 0 \
                or np.sum(s_test == 1) == 0 or np.sum(s_test == 0) == 0 \
                or np.sum(s_train == 1) == 0 or np.sum(s_train == 0) == 0:
            return run_test(dataset_name, dataset, target_c, run_number)

        oracle_pred = oracle_prediction(X_train, y_train, X_test)
        oracle_df = get_oracle_metrics(y_test, oracle_pred)
        oracle_df = oracle_df.assign(Dataset=dataset_name, Method='Oracle', c=target_c, RunNumber=run_number)
        oracle_df = pd.concat([
            oracle_df.assign(ConstC=True),
            oracle_df.assign(ConstC=False),
        ])
        oracle_df.to_csv(os.path.join(RESULTS_ROOT_DIR, dataset_name, 'oracle',
                                      f'{dataset_name}_{np.round(target_c, 1)}_{run_number}.csv'))

        dfs = []
        for clf_name in joint_classifiers:
            print(f'--- {dataset_name} ({clf_name}): c = {target_c}, run {run_number + 1}/{total_runs + first_run_index} ---')
            df = calculate_metrics(joint_classifiers[clf_name], X_train, y_train, s_train, X_test, y_test, c, oracle_pred)
            df = df.assign(Dataset=dataset_name, Method=clf_name, c=target_c, RunNumber=run_number, ConstC=False)

            df.to_csv(os.path.join(RESULTS_ROOT_DIR, dataset_name,
                                   f'{dataset_name}_{clf_name}_{np.round(target_c, 1)}_{run_number}_{False}.csv'))
            dfs.append(df)
        for clf_name in const_c_classifiers:
            print(f'--- {dataset_name} ({clf_name}): c = {target_c}, run {run_number + 1}/{total_runs + first_run_index} (CONST c) ---')
            df = calculate_metrics(const_c_classifiers[clf_name], X_train, y_train, s_train, X_test, y_test, c, oracle_pred,
                                   const_c=True)
            df = df.assign(Dataset=dataset_name, Method=clf_name, c=target_c, RunNumber=run_number, ConstC=True)

            df.to_csv(os.path.join(RESULTS_ROOT_DIR, dataset_name,
                                   f'{dataset_name}_{clf_name}_{np.round(target_c, 1)}_{run_number}_{True}.csv'))
            dfs.append(df)
        return pd.concat(dfs), oracle_df
    except cvxpy.error.SolverError:
        return run_test(dataset_name, dataset, target_c, run_number)


if __name__ == '__main__':
    if not os.path.exists(RESULTS_ROOT_DIR):
        os.mkdir(RESULTS_ROOT_DIR)
    for dataset in used_datasets:
        if not os.path.exists(os.path.join(RESULTS_ROOT_DIR, dataset)):
            os.mkdir(os.path.join(RESULTS_ROOT_DIR, dataset))
        if not os.path.exists(os.path.join(RESULTS_ROOT_DIR, dataset, 'oracle')):
            os.mkdir(os.path.join(RESULTS_ROOT_DIR, dataset, 'oracle'))

    data = datasets.get_datasets()
    data = {x: data[x] for x in used_datasets}
    c_values = np.arange(0.1, 1, 0.1)

    num_cores = multiprocessing.cpu_count() - 1
    results = Parallel(n_jobs=num_cores)(delayed(run_test)(dataset_name, data[dataset_name],
                                                           c, first_run_index + run_number)
                                         for dataset_name, run_number, c in zip(
                                             np.repeat(np.repeat(list(data.keys()), len(c_values)), total_runs),
                                             np.tile(np.repeat(range(total_runs), len(c_values)), len(data)),
                                             np.tile(np.tile(c_values, total_runs), len(data))
                                         ))

    metrics_dfs = [res[0] for res in results]
    oracle_dfs = [res[1] for res in results]
